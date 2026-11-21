import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import Http404, HttpRequest
from django.template import Context, Engine, Template, TemplateSyntaxError
from django.template.base import UNKNOWN_SOURCE, NodeList
from django.test import Client, RequestFactory, override_settings

import next as next_package
from next.components import (
    ComponentContextConflictError,
    ComponentInfo,
    components_manager,
)
from next.components.facade import render_component_error, render_component_loading
from next.components.info import BoundaryFiles
from next.components.loading import ModuleLoader
from next.components.trip import TripInput, prop_types
from next.defer import (
    DEFERRALS_KEY,
    BoundaryError,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    ErrorKind,
    stable_key,
)
from next.diagnostics import degraded, watch_degraded
from next.seeding import COLLECTOR_KEY
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.static.runtime import BUNDLE_NOTE
from next.suspense import (
    Delta,
    assembled,
    boundary_rendered,
    boundary_side_effect,
    deferring,
    finish,
    run_sequentially,
)
from next.suspense.components import Deferring
from next.suspense.session import BoundarySession
from next.suspense.tokens import decode_props
from next.suspense.tokens.props import DEFAULT_LIMIT
from next.suspense.trip import claims_for
from next.templatetags import components as component_tags
from next.templatetags.components import ComponentNode, _anchored
from next.testing import (
    boundary_errors,
    boundary_gate,
    capture_signals,
    parallel_boundaries,
)
from next.testing.boundaries import in_document_order, recorded_failures
from tests.pages.checks.metadata.trees import COMPONENTS
from tests.support import routed
from tests.support.pages import write_page


BOOM = "boom"
_HEAD = "from next.components import component\n"
_LOGIN = _HEAD + "component.login_required()\n"
_OBJECT = (
    _HEAD
    + 'component.permission_required("notes.read", obj="account")\n'
    + '@component.context("account")\ndef account():\n    return "acme"\n'
)


def _boom() -> str:
    raise ValueError(BOOM)


class _User:
    is_authenticated = True
    is_active = True

    def __init__(self, allowed: str = "acme") -> None:
        self.allowed = allowed

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return obj == self.allowed


def stamp(request: HttpRequest) -> dict[str, str]:
    return {"stamp": f"fresh:{request.path}"}


def _bundle_url(name: str) -> str:
    return f"/static/next/next.{name}.min.js"


def _request(user: object) -> HttpRequest:
    request = RequestFactory().get("/")
    request.user = user
    return request


class _Site:
    """Components resolved by name, rendered from template strings, jobs included."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.infos: dict[str, ComponentInfo] = {}
        self.collector = StaticCollector()

    def add(
        self,
        name: str,
        body: str,
        *,
        error: str | None = None,
        loading: str | None = None,
        module: str | None = None,
    ) -> ComponentInfo:
        folder = self.root / name
        folder.mkdir()
        (folder / "component.djx").write_text(body)
        paths: dict[str, Path | None] = {"error": None, "loading": None}
        for kind, text in (("error", error), ("loading", loading)):
            if text is not None:
                paths[kind] = folder / f"{kind}.djx"
                paths[kind].write_text(text)
        module_path = None
        if module is not None:
            module_path = folder / "component.py"
            module_path.write_text(module)
            ModuleLoader().load(module_path)
        files = None
        if paths["error"] is not None or paths["loading"] is not None:
            files = BoundaryFiles(paths["loading"], paths["error"])
        info = ComponentInfo(
            name=name,
            scope_root=self.root,
            scope_relative="",
            template_path=folder / "component.djx",
            module_path=module_path,
            is_simple=False,
            files=files,
        )
        self.infos[name] = info
        return info

    def collect(self, info: ComponentInfo, collector: StaticCollector | None) -> None:
        if collector is not None:
            collector.add(StaticAsset(url=f"/{info.name}.css", kind="css"))

    def context(self, **values: object) -> dict[str, object]:
        return {
            "current_template_path": str(self.root / "page.djx"),
            COLLECTOR_KEY: self.collector,
            "boom": _boom,
            **values,
        }

    def render(self, source: str, **values: object) -> str:
        return Template(source).render(Context(self.context(**values)))

    def deferred(
        self, source: str, **values: object
    ) -> tuple[str, BoundarySession[Delta]]:
        with deferring() as held:
            shell = self.render(source, **values)
        assert held.session is not None
        return shell, held.session

    def finished(self, source: str, **values: object) -> str:
        shell, session = self.deferred(source, **values)
        return finish(shell, session, self.collector, run_sequentially)

    def styles(self) -> list[str]:
        return [asset.url for asset in self.collector.assets_in_slot("styles")]


@pytest.fixture()
def site(tmp_path: Path) -> Iterator[_Site]:
    held = _Site(tmp_path)
    with (
        patch.object(
            components_manager,
            "get_component",
            side_effect=lambda name, _path: held.infos.get(name),
        ),
        patch("next.templatetags.components.collect_component_assets", held.collect),
    ):
        yield held


_KEY = stable_key(UNKNOWN_SOURCE, 0)


def _declaring(**modes: str):
    """Patch the declaration store so each named component declares its mode."""
    declared = {name: BoundaryOptions.parse(mode) for name, mode in modes.items()}
    store = SimpleNamespace(
        options=lambda path: None if path is None else declared.get(path.parent.name)
    )
    return patch("next.templatetags.components.defer_declarations", store)


class TestModeDecision:
    """The call site wins over the module, which wins over a `loading.djx`."""

    @pytest.mark.parametrize(
        ("loading", "declared", "tag", "jobs", "state"),
        [
            pytest.param(None, None, "", None, None, id="plain"),
            pytest.param("L", None, "", 1, "loading", id="loading-file"),
            pytest.param("L", None, " defer='inline'", 0, "ready", id="call-inline"),
            pytest.param(None, None, " defer='stream'", 1, "loading", id="call-stream"),
            pytest.param("L", "inline", "", 0, "ready", id="module-inline"),
            pytest.param(None, "visible", "", 1, "loading", id="client-trip"),
            pytest.param(None, "stream", " defer='inline'", 0, "ready", id="wins"),
        ],
    )
    def test_the_effective_mode(
        self,
        site: _Site,
        loading: str | None,
        declared: str | None,
        tag: str,
        jobs: int | None,
        state: str | None,
    ) -> None:
        site.add("card", "<c>C</c>", loading=loading, module=_HEAD)
        modes = {} if declared is None else {"card": declared}
        with _declaring(**modes), deferring() as held:
            html = site.render(f"{{% component 'card'{tag} %}}")
        if state is None:
            assert html == "<c>C</c>"
        else:
            assert f'data-next-state="{state}"' in html
        count = None if held.session is None else len(held.session.jobs)
        assert count == jobs

    def test_a_client_trip_without_a_request_queues_a_stream_job(
        self, site: _Site
    ) -> None:
        site.add("card", "C")
        _, session = site.deferred("{% component 'card' defer='manual' timeout=2 %}")
        job = session.jobs[0]
        assert job.site.mode is DeferMode.STREAM
        assert job.options.mode is DeferMode.STREAM
        assert job.options.timeout == pytest.approx(2.0)

    def test_the_call_site_timeout_wins_over_the_module(self, site: _Site) -> None:
        site.add("card", "C", module=_HEAD)
        store = SimpleNamespace(
            options=lambda _path: BoundaryOptions.parse(timeout="5s")
        )
        with patch("next.templatetags.components.defer_declarations", store):
            _, session = site.deferred("{% component 'card' defer='stream' %}")
            _, timed = site.deferred(
                "{% component 'card' defer='stream' timeout='1s' %}"
            )
        assert session.jobs[0].options.timeout == pytest.approx(5.0)
        assert timed.jobs[0].options.timeout == pytest.approx(1.0)

    def test_reload_and_delay_are_reserved_durations(self, site: _Site) -> None:
        site.add("card", "[{{ reload }}{{ delay }}]")
        _, session = site.deferred(
            "{% component 'card' defer='stream' reload='2s' delay='150ms' %}"
        )
        options = session.jobs[0].options
        assert options.reload == pytest.approx(2.0)
        assert options.delay == pytest.approx(0.15)
        assert site.finished("{% component 'card' defer='stream' reload=1 %}").endswith(
            "[]</div>"
        )

    def test_the_module_pacing_stands_without_a_call_site_one(
        self, site: _Site
    ) -> None:
        site.add("card", "C", module=_HEAD)
        written = BoundaryOptions.parse().paced(reload="3s", delay="1s")
        store = SimpleNamespace(options=lambda _path: written)
        with patch("next.templatetags.components.defer_declarations", store):
            _, session = site.deferred("{% component 'card' timeout='1s' %}")
        options = session.jobs[0].options
        assert (options.reload, options.delay) == (3.0, 1.0)

    def test_a_call_site_mode_marks_the_project_deferring(self) -> None:
        with patch(
            "next.components.registry.deferred_components", new=Mock(held=False)
        ) as flag:
            Template("{% component 'c' defer='stream' %}")
        flag.declare.assert_called_once_with()

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("{% component 'c' reload='soon' %}", "refuses its boundary options"),
            ("{% component 'c' delay=-1 %}", "refuses its boundary options"),
            ("{% component 'c' defer='nope' %}", "refuses its boundary options"),
            ("{% component 'c' defer=mode %}", "takes defer= as a literal"),
            ("{% component 'c' timeout=wait|default:1 %}", "takes timeout= as a lit"),
            ("{% component 'c' timeout='2' %}", "duration literal"),
            ("{% #component 'c' timeout='{{ x }}' %}{% /component %}", "a literal"),
        ],
    )
    def test_a_misfit_option_is_a_compile_error(
        self, source: str, message: str
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match=message):
            Template(source)

    def test_the_options_never_reach_the_props(self, site: _Site) -> None:
        site.add("card", "[{{ defer }}{{ timeout }}]")
        assert site.render("{% component 'card' defer='inline' timeout=1 %}").endswith(
            "[]</div>"
        )


class TestZeroCost:
    """A component with no boundary file and no declaration never reads a sink."""

    def test_a_plain_render_touches_no_sink(self, site: _Site) -> None:
        site.add("plain", "<p>{{ x }}</p>")
        sink = Mock()
        slot = Mock()
        with patch("next.components.boundary.deferrals_slot", slot):
            html = site.render("{% component 'plain' x=1 %}", **{DEFERRALS_KEY: sink})
        assert html == "<p>1</p>"
        assert sink.mock_calls == []
        assert slot.mock_calls == []

    def test_an_error_boundary_alone_takes_the_guarded_path(self, site: _Site) -> None:
        site.add("card", "{{ boom }}", error="E")
        slot = Mock()
        with patch("next.components.boundary.deferrals_slot", slot):
            html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert slot.mock_calls == []


class TestSink:
    """The context sink comes first, then the port, then a render in place."""

    def test_the_context_sink_takes_the_call(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        sink = Mock()
        sink.queue.return_value = "queued"
        assert (
            site.render("{% component 'card' %}", **{DEFERRALS_KEY: sink}) == "queued"
        )

    def test_without_any_sink_the_call_renders_behind_its_boundary(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ boom }}", loading="L", error="E")
        slot = Mock()
        slot.peek.return_value = None
        with patch("next.components.boundary.deferrals_slot", slot):
            html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert html.endswith('data-next-error="error">E</div>')

    def test_outside_a_deferring_block_the_call_renders_in_place(
        self, site: _Site
    ) -> None:
        site.add("card", "<c>C</c>", loading="L")
        html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert html.endswith(
            f'data-next-boundary-key="{_KEY}" data-next-state="ready"><c>C</c></div>'
        )

    def test_failing_props_render_in_place_behind_the_boundary(
        self, site: _Site
    ) -> None:
        site.add("card", "C", loading="L", error="E")
        with deferring() as held:
            html = site.render("{% component 'card' title=boom %}")
        assert held.session is None
        assert html.endswith('data-next-error="error">E</div>')


class TestLoadingStash:
    """An inline call inside a zone reloading to loading stashes its loading UI."""

    _LOOP = "{% for n in rows %}{% component 'card' defer='inline' %}{% endfor %}"

    def test_a_loading_zone_stashes_each_key_once(self, site: _Site) -> None:
        site.add("card", "<c>C</c>", loading="<i>{{ who }}</i>")
        html = site.render(
            '{% zone "z" reload="loading" %}' + self._LOOP + "{% endzone %}",
            rows=[1, 2],
            who="w",
        )
        stash = f'<template data-next-loading="{_KEY}"><i>w</i></template>'
        assert html.count(stash) == 1
        assert html.count('data-next-state="ready"') == 2

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param('{% zone "z" %}' + _LOOP + "{% endzone %}", id="keep-zone"),
            pytest.param(_LOOP, id="no-zone"),
        ],
    )
    def test_elsewhere_nothing_is_stashed(self, site: _Site, source: str) -> None:
        site.add("card", "<c>C</c>", loading="<i>L</i>")
        assert "data-next-loading" not in site.render(source, rows=[1])

    def test_a_call_without_loading_ui_stashes_nothing(self, site: _Site) -> None:
        site.add("card", "<c>C</c>")
        html = site.render(
            '{% zone "z" reload="loading" %}' + self._LOOP + "{% endzone %}", rows=[1]
        )
        assert "data-next-loading" not in html


class TestWrapper:
    """The shell holds the loading wrapper, the assembled page its settled form."""

    def test_the_loading_wrapper_exactly(self, site: _Site) -> None:
        site.add("card", "<c>C</c>", loading="L")
        shell, session = site.deferred("{% component 'card' %}")
        ident = session.jobs[0].id
        assert shell == (
            f'<!--{ident}:div--><div id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{_KEY}" data-next-state="loading" '
            f'aria-busy="true">L</div><!--/{ident}-->'
        )
        assert finish(shell, session, site.collector, run_sequentially) == (
            f'<div id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{_KEY}" data-next-state="ready"><c>C</c></div>'
        )

    def test_the_id_follows_the_session(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        shell, session = site.deferred("{% component 'card' %}{% component 'card' %}")
        assert [job.id for job in session.jobs] == [
            f"b-{session.seal}-0",
            f"b-{session.seal}-1",
        ]
        assert "card" not in shell

    def test_an_adopted_root_carries_the_boundary(self, site: _Site) -> None:
        site.add("row", "<tr><td>{{ n }}</td></tr>", loading="<tr><td>.</td></tr>")
        shell, session = site.deferred("<table>{% component 'row' n=1 %}</table>")
        ident = session.jobs[0].id
        assert shell == (
            f'<table><!--{ident}:--><tr id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{_KEY}" data-next-state="loading" '
            f'aria-busy="true"><td>.</td></tr><!--/{ident}--></table>'
        )
        assert finish(shell, session, site.collector, run_sequentially) == (
            f'<table><tr id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{_KEY}" data-next-state="ready"><td>1</td>'
            "</tr></table>"
        )

    def test_debug_names_the_owning_file(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            shell, _ = site.deferred("{% component 'card' %}")
        assert 'data-next-boundary-source="card/loading.djx"' in shell

    def test_debug_names_the_module_without_a_boundary_file(self, site: _Site) -> None:
        site.add("card", "C", module=_HEAD)
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            shell, _ = site.deferred("{% component 'card' defer='stream' %}")
        assert 'data-next-boundary-source="card/component.py"' in shell


class TestKey:
    """The key is positional, the same for every response and every user."""

    def test_two_renders_for_two_users_share_the_key(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        source = Template("{% component 'card' %}{% component 'card' %}")
        keys = []
        for user in (AnonymousUser(), _User()):
            with deferring() as held:
                source.render(Context(site.context(request=_request(user))))
            assert held.session is not None
            keys.append([job.key for job in held.session.jobs])
        assert keys[0] == keys[1]
        assert keys[0] == [_KEY, stable_key(UNKNOWN_SOURCE, 1)]

    def test_a_keyed_row_appends_its_key(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        _, session = site.deferred(
            "{% for p in items %}<li class='r' data-next-key=\"row-{{ p }}\">"
            "<span>{% component 'card' %}</span></li>{% endfor %}",
            items=["a", "b"],
        )
        assert [job.key for job in session.jobs] == [f"{_KEY}:row-a", f"{_KEY}:row-b"]

    @pytest.mark.parametrize(
        "source",
        [
            "{% for p in items %}<li>{% component 'card' %}</li>{% endfor %}",
            "<li data-next-key='x'>{% component 'card' %}</li>",
            "{% for p in items %}{% endfor %}{% component 'card' %}",
            (
                "{% for p in items %}<li data-next-key='{{ p }}'></li>"
                "{% component 'card' %}{% endfor %}"
            ),
        ],
    )
    def test_a_call_outside_a_keyed_row_keeps_the_site_key(
        self, site: _Site, source: str
    ) -> None:
        site.add("card", "C", loading="L")
        _, session = site.deferred(source, items=["a"])
        assert [job.key for job in session.jobs] == [_KEY]

    @pytest.mark.parametrize(
        ("row", "key"),
        [
            ("<li data-next-key='{{ p }}'></span>", "a"),
            ("<li data-next-key='const'><b></b>", "const"),
            ('<li data-next-key="{{ p }}-{{ p }}"><img src="x">', "a-a"),
        ],
    )
    def test_the_row_key_reads_the_open_element(
        self, site: _Site, row: str, key: str
    ) -> None:
        site.add("card", "C", loading="L")
        _, session = site.deferred(
            f"{{% for p in items %}}{row}{{% component 'card' %}}{{% endfor %}}",
            items=["a"],
        )
        assert [job.key for job in session.jobs] == [f"{_KEY}:{key}"]

    def test_a_row_inside_a_block_is_found(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        engine = Engine(
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        "base.html": "<ul>{% block rows %}{% endblock %}</ul>",
                        "page.html": "{% extends 'base.html' %}{% block rows %}"
                        "{% for p in items %}<li data-next-key='{{ p }}'>"
                        "{% component 'card' %}</li>{% endfor %}{% endblock %}",
                    },
                )
            ],
            builtins=["next.templatetags.components"],
        )
        with deferring() as held:
            engine.get_template("page.html").render(Context(site.context(items=["a"])))
        assert held.session is not None
        assert held.session.jobs[0].key == f"{stable_key('page.html', 0)}:a"

    def test_the_row_recipe_is_kept_per_template(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        template = Template(
            "{% for p in items %}<li data-next-key='{{ p }}'>"
            "{% component 'card' %}</li>{% endfor %}"
        )
        for _ in range(2):
            with deferring() as held:
                template.render(Context(site.context(items=["a"])))
            assert held.session is not None
            assert held.session.jobs[0].key == f"{_KEY}:a"


class TestDeclaredTag:
    """A declared tag overrides the markup before the call."""

    @pytest.mark.parametrize(
        ("tag", "opening"), [("section", "<section id="), (None, "<p id=")]
    )
    def test_the_declared_tag_wraps(
        self, site: _Site, tag: str | None, opening: str
    ) -> None:
        site.add("card", "C", loading="<p>L</p>", module=_HEAD)
        store = SimpleNamespace(
            options=lambda _path: BoundaryOptions.parse("stream", tag=tag)
        )
        with patch("next.templatetags.components.defer_declarations", store):
            shell, _ = site.deferred("{% component 'card' %}")
        assert opening in shell


class TestDetached:
    """A node rendered outside any template still defers."""

    def test_a_bare_node_queues_and_runs(self, site: _Site) -> None:
        site.add("card", "<c>{{ v }}</c>", loading="L")
        node = ComponentNode("card", {}, NodeList())
        with deferring() as held:
            shell = node.render(Context(site.context(v=1)))
        assert held.session is not None
        job = held.session.jobs[0]
        assert job.key == stable_key("", 0)
        assert job.site.origin == ""
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert html.endswith('data-next-state="ready"><c>1</c></div>')

    def test_debug_names_nothing_for_a_component_without_files(
        self, site: _Site
    ) -> None:
        site.infos["ghost"] = ComponentInfo(
            "ghost", site.root, "", None, None, is_simple=True
        )
        with override_settings(DEBUG=True):
            shell, _ = site.deferred("{% component 'ghost' defer='stream' %}")
        assert "data-next-boundary-source" not in shell


class TestCapture:
    """A job reads its call site by value, as the shell left it."""

    def test_each_loop_job_sees_its_own_iteration(self, site: _Site) -> None:
        site.add(
            "card",
            "[{{ forloop.counter }}.{{ forloop.parentloop.counter }}{{ item }}]",
            loading="L",
        )
        html = site.finished(
            "{% for outer in rows %}{% for item in items %}"
            "{% component 'card' %}{% endfor %}{% endfor %}",
            rows=[1, 2],
            items=["a", "b", "c"],
        )
        bodies = [part.split("]")[0] for part in html.split("[")[1:]]
        assert bodies == ["1.1a", "2.1b", "3.1c", "1.2a", "2.2b", "3.2c"]

    def test_a_slot_under_autoescape_off_stays_unescaped(self, site: _Site) -> None:
        site.add("card", "<c>{% set_slot 'x' %}</c>", loading="L")
        html = site.finished(
            "{% autoescape off %}{% #component 'card' %}{% #slot 'x' %}{{ h }}"
            "{% /slot %}{% /component %}{% endautoescape %}",
            h="<b>raw</b>",
        )
        assert "<c><b>raw</b></c>" in html

    def test_an_escaped_slot_stays_escaped(self, site: _Site) -> None:
        site.add("card", "<c>{% set_slot 'x' %}</c>", loading="L")
        html = site.finished(
            "{% #component 'card' %}{% #slot 'x' %}{{ h }}{% /slot %}{% /component %}",
            h="<b>",
        )
        assert "<c>&lt;b&gt;</c>" in html

    def test_an_include_in_a_slot_finds_its_engine(self, site: _Site) -> None:
        site.add("card", "<c>{% set_slot 'x' %}</c>", loading="L")
        engine = Engine(
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        "part.html": "<i>{{ who }}</i>",
                        "page.html": "{% #component 'card' %}{% #slot 'x' %}"
                        "{% include 'part.html' %}{% /slot %}{% /component %}",
                    },
                )
            ],
            builtins=["next.templatetags.components"],
        )
        with deferring() as held:
            shell = engine.get_template("page.html").render(
                Context(site.context(who="W"))
            )
        assert held.session is not None
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert "<c><i>W</i></c>" in html

    def test_the_job_reruns_the_context_processors(self, site: _Site) -> None:
        site.add("card", "<c>{{ stamp }}</c>", loading="L{{ stamp }}")
        engine = Engine(
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {"page.html": "{% component 'card' %}"},
                )
            ],
            builtins=["next.templatetags.components"],
            context_processors=["tests.components.test_deferred.stamp"],
        )
        with deferring() as held:
            shell = engine.get_template("page.html").render(
                Context(site.context(request=_request(AnonymousUser())))
            )
        assert held.session is not None
        assert 'aria-busy="true">L</div>' in shell
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert "<c>fresh:/</c>" in html


class TestAccess:
    """A rule without a context object runs in the shell, one with it in the job."""

    @pytest.mark.parametrize(
        ("user", "shown"), [(AnonymousUser(), False), (_User(), True)]
    )
    def test_a_shell_rule_refuses_before_any_wrapper(
        self, site: _Site, user: object, *, shown: bool
    ) -> None:
        site.add("card", "<b>C</b>", loading="L{{ props.t }}", module=_LOGIN)
        with deferring() as held:
            shell = site.render(
                "[{% component 'card' t='T' %}]", request=_request(user)
            )
        if shown:
            assert "LT</div>" in shell
            assert held.session is not None
        else:
            assert shell == "[]"
            assert held.session is None

    @pytest.mark.parametrize(("allowed", "body"), [("acme", "<b>T</b>"), ("x", "")])
    def test_a_rule_reading_the_context_runs_in_the_job(
        self, site: _Site, allowed: str, body: str
    ) -> None:
        site.add("card", "<b>{{ t }}</b>", loading="L[{{ props.t }}]", module=_OBJECT)
        shell, session = site.deferred(
            "{% component 'card' t='T' %}", request=_request(_User(allowed))
        )
        assert "L[]</div>" in shell
        html = finish(shell, session, site.collector, run_sequentially)
        assert html.endswith(f'data-next-state="ready">{body}</div>')


class TestLoading:
    """The loading UI reads the call site and the props, a failure its error UI."""

    def test_the_call_site_and_the_props(self, site: _Site) -> None:
        site.add("card", "C", loading="{{ page }}|{{ props.title }}|{{ title }}")
        shell, _ = site.deferred("{% component 'card' title='T' %}", page="P")
        assert ">P|T|</div>" in shell

    def test_without_a_file_or_a_default_the_wrapper_is_empty(
        self, site: _Site
    ) -> None:
        site.add("card", "C")
        shell, _ = site.deferred("{% component 'card' defer='stream' %}")
        assert 'aria-busy="true"></div>' in shell

    def test_an_adopted_root_without_loading_ui_is_a_template(
        self, site: _Site
    ) -> None:
        site.add("row", "<tr></tr>")
        shell, session = site.deferred(
            "<table>{% component 'row' defer='stream' %}</table>"
        )
        ident = session.jobs[0].id
        assert f'<template id="{ident}"' in shell

    def test_a_broken_loading_file_falls_to_the_error_ui(self, site: _Site) -> None:
        site.add("card", "C", loading="{{ boom }}", error="E:{{ error.kind }}")
        shell, session = site.deferred("{% component 'card' %}")
        assert 'aria-busy="true">E:error</div>' in shell
        assert "data-next-error" not in shell
        html = finish(shell, session, site.collector, run_sequentially)
        assert html.endswith('data-next-state="ready">C</div>')

    def test_the_project_default_stands_in(self, site: _Site) -> None:
        site.add("card", "C")
        template = Mock()
        template.render.return_value = "DEFAULT"
        with (
            override_settings(NEXT_FRAMEWORK={"DEFER": {"LOADING": "skeleton.html"}}),
            patch("next.components.facade.get_template", return_value=template) as get,
        ):
            shell, _ = site.deferred("{% component 'card' defer='stream' %}")
        get.assert_called_once_with("skeleton.html")
        assert 'aria-busy="true">DEFAULT</div>' in shell


class TestJob:
    """A job renders through the same boundary an inline render meets."""

    def test_a_failing_job_renders_its_error_ui_and_rolls_its_assets_back(
        self, site: _Site
    ) -> None:
        site.add("leaf", "x")
        site.add(
            "bad",
            "{% component 'leaf' %}{{ boom }}",
            loading="L",
            error="E:{{ error.kind }}",
        )
        site.add("good", "{% component 'leaf' %}G", loading="L")
        watch_degraded()
        html = site.finished("{% component 'good' %}{% component 'bad' %}")
        assert 'data-next-state="error" data-next-error="error">E:error</div>' in html
        assert site.styles() == ["/good.css", "/bad.css", "/leaf.css"]
        assert site.collector._journal is None
        assert degraded() is True

    def test_a_debug_failure_shows_what_the_inline_boundary_shows(
        self, site: _Site
    ) -> None:
        site.add("bad", "{{ boom }}", loading="L", error="E")
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            html = site.finished("{% component 'bad' %}")
        assert 'data-next-state="error" data-next-error="error" ' in html
        assert "data-next-error-digest=" in html
        assert "<details open data-next-error-detail>" in html

    def test_a_render_meeting_a_deferred_call_marks_the_project_deferring(
        self, site: _Site
    ) -> None:
        site.add("card", "C", loading="L")
        with patch(
            "next.components.registry.deferred_components", new=Mock(held=False)
        ) as flag:
            site.render("{% component 'card' %}")
        flag.declare.assert_called_once_with()

    def test_a_failing_job_without_error_ui_reads_as_an_error(
        self, site: _Site
    ) -> None:
        site.add("bad", "{{ boom }}", loading="L")
        html = site.finished("{% component 'bad' %}")
        assert html.endswith('data-next-state="error" data-next-error="error"></div>')

    def test_the_timeout_fallback_never_runs_the_component(self, site: _Site) -> None:
        site.add(
            "slow", "{{ boom }}", loading="L", error="{{ error.kind }}|{{ props.t }}"
        )
        _, session = site.deferred("{% component 'slow' t='T' %}")
        rendered = session.jobs[0].fallback(ErrorKind.TIMEOUT)
        assert rendered.html == "timeout|T"
        assert rendered.kind is ErrorKind.TIMEOUT

    def test_an_opaque_overload_reads_as_an_error(self, site: _Site) -> None:
        site.add("slow", "C", loading="L", error="{{ error.kind }}")
        _, session = site.deferred("{% component 'slow' %}")
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"ERRORS": {"KINDS": "opaque"}}}
        ):
            rendered = session.jobs[0].fallback(ErrorKind.OVERLOADED)
        assert rendered.html == "error"
        assert rendered.kind is ErrorKind.ERROR

    def test_the_error_default_stands_in(self, site: _Site) -> None:
        site.add("slow", "C", loading="L")
        _, session = site.deferred("{% component 'slow' %}")
        template = Mock()
        template.render.return_value = "OOPS"
        with (
            override_settings(NEXT_FRAMEWORK={"DEFER": {"ERROR": "oops.html"}}),
            patch("next.components.facade.get_template", return_value=template),
        ):
            rendered = session.jobs[0].fallback(ErrorKind.TIMEOUT)
        assert rendered.html == "OOPS"


class TestNested:
    """A deferred call inside a job joins it, up to `MAX_DEPTH` levels."""

    def test_a_nested_call_is_recorded(self, site: _Site) -> None:
        site.add("inner", "<i>{{ v }}</i>", loading="li")
        site.add("outer", "<o>{% component 'inner' v=1 %}</o>", loading="lo")
        _, session = site.deferred("{% component 'outer' %}")
        parent = session.jobs[0]
        rendered = parent.run()
        assert [(job.parent, job.depth) for job in rendered.nested] == [(parent.id, 1)]
        assert ">li</div>" in rendered.html

    def test_past_the_depth_a_nested_call_renders_in_place(
        self, site: _Site, caplog: pytest.LogCaptureFixture
    ) -> None:
        site.add("inner", "<i>{{ v }}</i>", loading="li")
        site.add("outer", "<o>{% component 'inner' v=1 %}</o>", loading="lo")
        with override_settings(DEBUG=True, NEXT_FRAMEWORK={"DEFER": {"MAX_DEPTH": 1}}):
            _, session = site.deferred("{% component 'outer' %}")
            with caplog.at_level("DEBUG", logger="next.suspense"):
                rendered = session.jobs[0].run()
                session.jobs[0].run()
        assert rendered.nested == ()
        assert 'data-next-state="ready"><i>1</i></div>' in rendered.html
        assert len([r for r in caplog.records if "MAX_DEPTH" in r.getMessage()]) == 1

    def test_a_nested_outcome_lands_inside_its_parent(self, site: _Site) -> None:
        site.add("inner", "<i>{{ v }}</i>", loading="li")
        site.add("outer", "<o>{% component 'inner' v=1 %}</o>", loading="lo")
        html = site.finished("{% component 'outer' %}")
        assert html.count('data-next-state="ready"') == 2
        assert "<o><div id=" in html
        assert "<!--" not in html
        assert site.styles() == ["/outer.css", "/inner.css"]


def _nap() -> str:
    time.sleep(0.15)
    return ""


class _Meeting:
    """Lets each of three jobs through only once all three are in flight at once."""

    def __init__(self) -> None:
        self.barrier = threading.Barrier(3, timeout=5)

    def __call__(self) -> str:
        self.barrier.wait()
        return ""


def _missing() -> str:
    raise Http404


def _write_session(request: HttpRequest) -> str:
    request.session["seen"] = True
    return ""


class TestAssembled:
    """Every job a render queued runs in parallel and lands in its wrapper whole."""

    def _assembled(
        self,
        site: _Site,
        source: str,
        request: HttpRequest | None = None,
        **values: object,
    ) -> str:
        if request is not None:
            values["request"] = request
        return assembled(
            lambda: site.render(source, **values),
            collector=site.collector,
            request=request,
        )

    def test_three_slow_jobs_run_side_by_side_in_document_order(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ meet }}<c>{{ n }}</c>", loading="L")
        source = "".join(f"{{% component 'card' n={n} %}}" for n in (1, 2, 3))
        meeting = _Meeting()
        with parallel_boundaries():
            html = self._assembled(site, source, meet=meeting)
        assert meeting.barrier.broken is False, "the three jobs never ran at once"
        assert html.count('data-next-state="ready"') == 3
        assert [html.index(f"<c>{n}</c>") for n in (1, 2, 3)] == sorted(
            html.index(f"<c>{n}</c>") for n in (1, 2, 3)
        )
        assert "<!--" not in html
        assert site.styles() == ["/card.css"]

    def test_a_failing_job_shows_its_error_ui_beside_the_others(
        self, site: _Site
    ) -> None:
        site.add("card", "<c>{{ n }}</c>", loading="L")
        site.add("bad", "{{ boom }}", loading="L", error="E:{{ error.kind }}")
        with boundary_errors("render"), parallel_boundaries():
            html = self._assembled(
                site, "{% component 'card' n=1 %}{% component 'bad' %}"
            )
        assert 'data-next-state="ready"><c>1</c>' in html
        assert 'data-next-error="error">E:error</div>' in html

    def test_a_job_past_its_timeout_settles_as_one(self, site: _Site) -> None:
        site.add("card", "{{ nap }}C", loading="L", error="E:{{ error.kind }}")
        with parallel_boundaries():
            html = self._assembled(
                site, "{% component 'card' timeout='20ms' %}", nap=_nap
            )
        assert 'data-next-error="timeout">E:timeout</div>' in html

    def test_an_intended_exception_in_a_job_raises_on_the_request_thread(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ missing }}", loading="L", error="E")
        with pytest.raises(Http404):
            self._assembled(site, "{% component 'card' %}", missing=_missing)

    def test_every_settled_job_is_announced(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        with capture_signals(boundary_rendered) as recorder:
            self._assembled(site, "{% component 'card' %}{% component 'card' %}")
        events = recorder.events_for(boundary_rendered)
        assert [event.kwargs["name"] for event in events] == ["card", "card"]
        first = events[0].kwargs
        assert first["state"] == "ready"
        assert first["kind"] is None
        assert first["mode"] is DeferMode.STREAM
        assert first["addressing"] == "component"
        assert first["reason"] is not None
        assert first["run_ms"] >= 0

    @override_settings(
        MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware"]
    )
    def test_a_session_write_in_a_job_is_reported(self, site: _Site) -> None:
        site.add("card", "{{ write }}C", loading="L")
        request = RequestFactory().get("/")
        request.session = SessionStore()
        with capture_signals(boundary_side_effect) as recorder:
            self._assembled(
                site,
                "{% component 'card' %}",
                request=request,
                write=lambda: _write_session(request),
            )
        [event] = recorder.events_for(boundary_side_effect)
        assert event.kwargs["kind"] == "session"
        assert event.kwargs["request"] is request

    def test_a_failure_inside_a_job_is_re_raised_by_the_test_helpers(
        self, site: _Site
    ) -> None:
        site.add("bad", "{{ boom }}", loading="L", error="E")
        with recorded_failures() as failures:
            self._assembled(site, "{% component 'bad' %}")
        assert [failure.name for failure in failures] == ["bad"]

    def test_a_gate_holds_a_job_until_the_test_releases_it(self, site: _Site) -> None:
        site.add("card", "<c>C</c>", loading="L")
        result: list[str] = []
        with parallel_boundaries(), boundary_gate(component="card") as gate:
            worker = threading.Thread(
                target=lambda: result.append(
                    self._assembled(site, "{% component 'card' %}")
                )
            )
            worker.start()
            assert gate.wait(5) is True
            assert result == []
            gate.release()
            worker.join(5)
        assert 'data-next-state="ready"><c>C</c>' in result[0]

    def test_a_render_inside_a_job_runs_as_it_is(self, site: _Site) -> None:
        inner = Mock(return_value="as is")
        site.add("outer", "{{ inner }}", loading="L")
        html = self._assembled(
            site,
            "{% component 'outer' %}",
            inner=lambda: assembled(inner, collector=None, request=None),
        )
        assert "as is" in html
        inner.assert_called_once_with()

    def test_a_render_queueing_nothing_comes_back_as_it_is(self, site: _Site) -> None:
        site.add("plain", "<p>P</p>")
        assert self._assembled(site, "{% component 'plain' %}") == "<p>P</p>"


def _engine(**templates: str) -> Engine:
    return Engine(
        loaders=[
            (
                "django.template.loaders.cached.Loader",
                [("django.template.loaders.locmem.Loader", templates)],
            )
        ],
        builtins=["next.templatetags.components"],
        libraries={"cache": "django.templatetags.cache"},
        context_processors=["tests.components.test_deferred.stamp"],
    )


def _jobs(held: Deferring) -> int:
    return 0 if held.session is None else len(held.session.jobs)


@pytest.fixture()
def deferring_project(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "next.templatetags.components.deferred_components", Mock(held=True)
    )


class TestStoredRender:
    """A call whose output a storing tag keeps renders inline, never as a skeleton."""

    def test_a_call_inside_cache_settles_in_place(self, site: _Site) -> None:
        site.add("card", "<c>C</c>", loading="L")
        with deferring() as held:
            html = site.render(
                "{% load cache %}{% cache 0 stored %}{% component 'card' %}"
                "{% endcache %}"
            )
        assert _jobs(held) == 0
        assert html.endswith('data-next-state="ready"><c>C</c></div>')

    def test_a_component_inside_cache_keeps_its_nested_calls_inline(
        self, site: _Site
    ) -> None:
        site.add("card", "<c>C</c>", loading="L")
        site.add("outer", "<o>{% component 'card' %}</o>")
        with deferring() as held:
            html = site.render(
                "{% load cache %}{% cache 0 nested %}{% component 'outer' %}"
                "{% endcache %}{% component 'card' %}"
            )
        assert _jobs(held) == 1
        assert '<o><div id="b-' in html
        assert "<!--" in html.split("</o>")[1]

    @pytest.mark.usefixtures("deferring_project")
    def test_an_include_inside_cache_settles_in_place(self, site: _Site) -> None:
        site.add("card", "<c>C</c>", loading="L")
        engine = _engine(
            **{
                "page.html": "{% load cache %}{% cache 0 inc %}"
                "{% include 'wrap.html' %}{% include 'wrap.html' %}{% endcache %}"
                "{% include 'free.html' %}",
                "wrap.html": "<w>{% include 'part.html' %}</w>",
                "part.html": "{% component 'card' %}",
                "free.html": "{% component 'card' %}",
            }
        )
        template = engine.get_template("page.html")
        for _ in range(2):
            with deferring() as held:
                template.render(Context(site.context()))
            assert _jobs(held) == 1

    @pytest.mark.usefixtures("deferring_project")
    def test_an_include_outside_cache_still_defers(self, site: _Site) -> None:
        site.add("card", "<c>C</c>", loading="L")
        engine = _engine(
            **{
                "page.html": "{% load cache %}{% cache 0 other %}x{% endcache %}"
                "{% include 'part.html' %}",
                "plain.html": "{% include 'part.html' %}",
                "part.html": "{% component 'card' %}",
            }
        )
        for name in ("page.html", "plain.html"):
            with deferring() as held:
                engine.get_template(name).render(Context(site.context()))
            assert _jobs(held) == 1


class TestShape:
    """A site adopting its root takes one root element, else it settles as an error."""

    def test_several_rows_settle_as_an_error(
        self, site: _Site, caplog: pytest.LogCaptureFixture
    ) -> None:
        site.add("rows", "<li>a</li><li>b</li>", loading="<li>.</li>")
        watch_degraded()
        with caplog.at_level("ERROR", logger="next.templatetags.components"):
            html = site.finished("<ul>{% component 'rows' %}</ul>")
        assert html.startswith("<ul><template id=")
        assert html.endswith(
            'data-next-state="error" data-next-error="error"></template></ul>'
        )
        assert "renders 2 root elements" in caplog.text
        assert "digest" in caplog.text
        assert degraded() is True

    def test_debug_names_the_template_and_the_line(self, site: _Site) -> None:
        site.add("rows", "loose", loading="<li>.</li>")
        with override_settings(DEBUG=True):
            html = site.finished("<ul>\n{% component 'rows' %}</ul>")
        assert "data-next-error-digest=" in html
        assert (
            'data-next-error-detail="the call of component &#x27;rows&#x27; in '
            "&lt;unknown source&gt;, line 2 renders loose text"
        ) in html

    def test_a_misfit_loading_ui_settles_the_shell_as_an_error(
        self, site: _Site
    ) -> None:
        site.add("rows", "<li>a</li>", loading="<li>1</li><li>2</li>")
        shell, session = site.deferred("<ul>{% component 'rows' %}</ul>")
        assert 'data-next-state="error"' in shell
        assert "aria-busy" not in shell
        html = finish(shell, session, site.collector, run_sequentially)
        assert html == (
            f'<ul><li id="{session.jobs[0].id}" '
            f'data-next-boundary="{session.jobs[0].id}" '
            f'data-next-boundary-key="{_KEY}" data-next-state="ready">a</li></ul>'
        )

    def test_an_inline_call_settles_a_misfit_too(self, site: _Site) -> None:
        site.add("rows", "<li>a</li><li>b</li>", loading="<li>.</li>")
        html = site.render("<ul>{% component 'rows' defer='inline' %}</ul>")
        assert 'data-next-state="error"' in html

    def test_an_empty_body_stays_a_ready_placeholder(self, site: _Site) -> None:
        site.add("rows", "", loading="<li>.</li>")
        html = site.finished("<ul>{% component 'rows' %}</ul>")
        assert html.endswith('data-next-state="ready"></template></ul>')


class TestDocumentOrder:
    """The shell takes a queued call's co-located assets where the call stands."""

    def test_a_deferred_call_keeps_its_place_in_the_cascade(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        site.add("plain", "P")
        site.finished("{% component 'card' %}{% component 'plain' %}")
        assert site.styles() == ["/card.css", "/plain.css"]


class TestFailureIds:
    """A contained failure reports the id of the wrapper the document carries."""

    def _ids(self, html: str) -> list[str]:
        return re.findall(r' id="(b-[0-9a-f]{8}-\d+)"', html)

    @pytest.mark.parametrize(
        ("module", "loading"),
        [
            pytest.param(_HEAD + 'component.defer("inline")\n', None, id="in-place"),
            pytest.param(None, "L", id="stream-job"),
        ],
    )
    def test_each_failure_names_its_wrapper(
        self, site: _Site, module: str | None, loading: str | None
    ) -> None:
        site.add("ok", "<i>ok</i>", loading=loading, module=module)
        site.add("bad", "{{ boom }}", error="E", loading=loading, module=module)
        source = (
            "{% component 'ok' %}{% component 'bad' %}"
            "{% component 'ok' %}{% component 'bad' %}"
        )
        with boundary_errors("render"), recorded_failures() as failures:
            html = site.finished(source, request=_request(None))
        recorded = [failure.id for failure in failures]
        assert len(recorded) == 2
        assert len(set(recorded)) == 2
        wrappers = self._ids(html)
        assert len(wrappers) == len(set(wrappers)) == 4
        assert recorded == [wrappers[1], wrappers[3]]
        assert in_document_order(failures[::-1], html) == tuple(failures)

    def test_a_nested_failure_keeps_its_own_id(self, site: _Site) -> None:
        site.add("inner", "{{ boom }}", error="I", loading="L")
        site.add("outer", "<o>{% component 'inner' %}</o>", error="O", loading="L")
        with boundary_errors("render"), recorded_failures() as failures:
            html = site.finished("{% component 'outer' %}", request=_request(None))
        (failure,) = failures
        assert failure.name == "inner"
        assert self._ids(html) == [self._ids(html)[0], failure.id]


class TestDeniedAssets:
    """A component its access rule denies adds no asset, deferred or inline."""

    def test_a_job_denied_by_its_rule_adds_nothing(self, site: _Site) -> None:
        site.add("card", "<b>{{ t }}</b>", loading="L", module=_OBJECT)
        site.finished("{% component 'card' t='T' %}", request=_request(_User("x")))
        assert site.styles() == []

    @pytest.mark.parametrize(
        ("user", "html", "styles"),
        [(AnonymousUser(), "", []), (_User(), "<b>C</b>", ["/card.css"])],
    )
    def test_an_inline_rule_keeps_the_assets_of_a_shown_component_alone(
        self, site: _Site, user: object, html: str, styles: list[str]
    ) -> None:
        site.add("card", "<b>C</b>", module=_LOGIN)
        assert site.render("{% component 'card' %}", request=_request(user)) == html
        assert site.styles() == styles
        assert site.collector._journal is None

    def test_a_ruled_component_that_raises_takes_its_assets_back(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ boom }}", module=_LOGIN)
        with pytest.raises(ValueError, match=BOOM):
            site.render("{% component 'card' %}", request=_request(_User()))
        assert site.styles() == []
        assert site.collector._journal is None

    def test_without_a_collector_the_render_runs_plainly(self, site: _Site) -> None:
        site.add("card", "<b>C</b>", module=_LOGIN)
        html = site.render(
            "{% component 'card' %}", request=_request(_User()), **{COLLECTOR_KEY: None}
        )
        assert html == "<b>C</b>"


class TestInsideLoading:
    """A deferred call inside a loading UI renders in place, with no job."""

    def test_the_spinner_renders_bare(self, site: _Site) -> None:
        site.add("spin", "<s>S</s>", loading="never")
        site.add("card", "C", loading="{% component 'spin' %}")
        shell, session = site.deferred("{% component 'card' %}")
        assert len(session.jobs) == 1
        assert 'aria-busy="true"><s>S</s></div>' in shell
        assert "never" not in shell

    def test_a_client_trip_call_renders_bare(self, site: _Site) -> None:
        site.collector = StaticCollector(bundle_url=_bundle_url)
        site.add("trip", "<b>{{ x }}</b>", loading="never", module=_TRIP)
        site.add("card", "C", loading="{% component 'trip' x=1 %}")
        with deferring(request=_request(AnonymousUser())) as held:
            shell = site.render("{% component 'card' %}")
        assert held.session is not None
        assert len(held.session.jobs) == 1
        assert "<b>1</b>" in shell
        assert "data-next-token" not in shell
        assert "never" not in shell
        assert site.collector.notes(BUNDLE_NOTE) == ()

    def test_a_zone_skeleton_renders_its_calls_bare(self, site: _Site) -> None:
        site.collector = StaticCollector(bundle_url=_bundle_url)
        site.add("trip", "<b>{{ x }}</b>", loading="never", module=_TRIP)
        site.add("slow", "<s>S</s>", loading="never")
        source = '{% zone "rows" reload="loading" %}<p>r</p>{% endzone %}'
        page = site.root / "list"
        page.mkdir()
        (page / "template.djx").write_text(source)
        (page / "loading.rows.djx").write_text(
            "{% component 'trip' x=1 %}{% component 'slow' defer='stream' %}"
        )
        with deferring(request=_request(AnonymousUser())) as held:
            shell = site.render(
                source, current_template_path=str(page / "template.djx")
            )
        assert '<template data-next-loading="rows"><b>1</b><s>S</s></template>' in (
            shell
        )
        assert not (held.session and held.session.jobs)
        assert "data-next-token" not in shell
        assert site.collector.notes(BUNDLE_NOTE) == [_bundle_url("reload")]

    def test_a_stashed_loading_ui_renders_its_calls_bare(self, site: _Site) -> None:
        site.collector = StaticCollector(bundle_url=_bundle_url)
        site.add("trip", "<b>{{ x }}</b>", loading="never", module=_TRIP)
        site.add("card", "<c>C</c>", loading="{% component 'trip' x=1 %}")
        with deferring(request=_request(AnonymousUser())):
            html = site.render(
                '{% zone "z" reload="loading" %}'
                "{% component 'card' defer='inline' %}{% endzone %}"
            )
        assert f'<template data-next-loading="{_KEY}"><b>1</b></template>' in html
        assert "data-next-token" not in html
        assert site.collector.notes(BUNDLE_NOTE) == [_bundle_url("reload")]


class TestCallSiteValues:
    """A job sees what the call site saw, over the processors and through one pass."""

    def test_a_with_value_sits_over_a_processor(self, site: _Site) -> None:
        site.add("card", "<c>{{ stamp }}</c>", loading="L")
        engine = _engine(
            **{
                "page.html": "{% with stamp='mine' %}{% component 'card' %}"
                "{% endwith %}"
            }
        )
        with deferring() as held:
            shell = engine.get_template("page.html").render(
                Context(site.context(request=_request(AnonymousUser())))
            )
        assert held.session is not None
        html = finish(shell, held.session, site.collector, run_sequentially)
        assert "<c>mine</c>" in html

    def test_a_generator_is_read_by_the_job_and_the_shell(self, site: _Site) -> None:
        site.add("gen", "<g>{% for r in rows %}{{ r }}{% endfor %}</g>", loading="L")
        html = site.finished(
            "{% component 'gen' %}{% for r in rows %}[{{ r }}]{% endfor %}",
            rows=(x for x in "ab"),
        )
        assert "<g>ab</g>" in html
        assert html.endswith("[a][b]")


class TestAnchoredKey:
    """The key hashes the template relative to what holds it, not the host path."""

    def test_an_include_used_twice_keys_each_render(self, site: _Site) -> None:
        site.add("card", "C", loading="L")
        engine = _engine(
            **{
                "page.html": "{% include 'part.html' %}{% include 'part.html' %}",
                "part.html": "{% component 'card' %}",
            }
        )
        with deferring() as held:
            engine.get_template("page.html").render(Context(site.context()))
        assert held.session is not None
        assert [job.key for job in held.session.jobs] == [
            stable_key("part.html", 0),
            stable_key("part.html", "0.1"),
        ]

    def test_a_relative_name_stands(self) -> None:
        assert _anchored("page.html") == "page.html"

    def test_an_app_file_is_named_by_its_app(self) -> None:
        inside = Path(next_package.__file__).resolve().parent / "x" / "t.djx"
        assert _anchored(str(inside)) == "next:x/t.djx"

    def test_a_root_file_is_named_by_its_root(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        entry = {"BACKEND": "next.urls.FileRouterBackend", "DIRS": [str(pages)]}
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [entry],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            assert _anchored(str(pages / "a" / "page.py")) == "0:a/page.py"

    def test_a_base_dir_file_is_named_by_its_path(self, tmp_path: Path) -> None:
        with override_settings(BASE_DIR=tmp_path):
            assert _anchored(str(tmp_path / "t" / "x.djx")) == "t/x.djx"

    def test_a_file_outside_everything_keeps_its_path(self, tmp_path: Path) -> None:
        with override_settings(BASE_DIR=None):
            assert _anchored(str(tmp_path / "far.djx")) == str(tmp_path / "far.djx")


class TestFacade:
    """The boundary files of a component render through its compiled templates."""

    def test_an_unreadable_loading_file_renders_nothing(self, site: _Site) -> None:
        info = site.add("card", "C", loading="L")
        assert info.files is not None
        assert info.files.loading is not None
        info.files.loading.unlink()
        assert render_component_loading(info, {}) == ""

    def test_a_component_without_files_renders_no_ui(self, tmp_path: Path) -> None:
        info = ComponentInfo("c", tmp_path, "", None, None, is_simple=False)
        assert render_component_loading(info, {}) == ""
        assert render_component_error(info, {}) == ""
        assert render_component_error(info, {}, deferred=True) == ""


_TRIP = _HEAD + 'component.defer("load", props=("x",))\n'


class TestClientTrip:
    """A client-trip call leaves a token wrapper and never runs its component."""

    def test_props_that_fail_to_resolve_render_in_place(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", error="E", loading="L", module=_TRIP)
        with deferring(request=_request(AnonymousUser())):
            shell = site.render("{% component 'card' x=boom %}")
        assert "data-next-token" not in shell
        assert "E" in shell
        assert "<b>" not in shell

    def test_children_are_refused(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        with pytest.raises(TemplateSyntaxError, match=r"next\.E224"):
            site.render("{% #component 'card' x=1 %}kid{% /component %}")

    def test_without_a_sink_the_call_renders_in_place(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        slot = SimpleNamespace(peek=lambda: None)
        with patch("next.components.boundary.deferrals_slot", slot):
            html = site.render("{% component 'card' x=1 %}", request=_request(None))
        assert "<b>1</b>" in html
        assert "data-next-token" not in html

    def test_the_request_of_the_deferring_block_mints(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        with deferring(request=_request(AnonymousUser())) as held:
            shell = site.render("{% component 'card' x='1' %}")
        assert 'data-next-defer="load"' in shell
        assert "data-next-token=" in shell
        assert held.session is not None
        assert not held.session.jobs

    @pytest.mark.parametrize("value", [False, None, 0, 1.5, ["a", 2]])
    def test_an_unannotated_prop_reloads_with_its_inline_value(
        self, site: _Site, value: object
    ) -> None:
        info = site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        request = _request(AnonymousUser())
        trip = TripInput(info, DeferMode.LOAD, {"x": value})
        claims = claims_for(request, trip, DEFAULT_LIMIT)
        decoded = decode_props(request, claims.props, prop_types(info))
        assert decoded == {"x": value}
        assert type(decoded["x"]) is type(value)

    def test_a_literal_false_prop_mints_a_token(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        with deferring(request=_request(AnonymousUser())):
            shell = site.render("{% component 'card' x=False %}")
        assert "data-next-token=" in shell

    def test_no_request_streams_the_call_instead(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        with deferring() as held:
            shell = site.render("{% component 'card' x=1 %}")
        assert "data-next-token" not in shell
        assert held.session is not None
        assert held.session.jobs[0].site.mode is DeferMode.STREAM

    def test_a_call_inside_a_stored_render_stays_inline(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        html = site.render(
            "{% load cache %}{% cache 60 trip %}{% component 'card' x=1 %}{% endcache %}"
        )
        assert "<b>1</b>" in html
        assert "data-next-token" not in html


_PROPS = _HEAD + 'component.props("x")\n'


class TestPlainRetry:
    """A plain guarded call that declares props retries with a token of its own."""

    def _failed(self, site: _Site, module: str, user: object) -> str:
        site.add("card", "<b>{{ x }}{{ boom }}</b>", error="E", module=module)
        return site.render("{% component 'card' x='1' %}", request=_request(user))

    def test_a_failed_call_carries_the_token_alone(self, site: _Site) -> None:
        html = self._failed(site, _PROPS, AnonymousUser())
        assert 'data-next-state="error"' in html
        assert "data-next-token=" in html
        assert "data-next-boundary-exp" not in html

    def test_a_call_whose_rule_passed_carries_the_token(self, site: _Site) -> None:
        html = self._failed(site, _PROPS + "component.login_required()\n", _User())
        assert "data-next-token=" in html

    @pytest.mark.parametrize(
        ("module", "user"),
        [
            pytest.param(_HEAD, AnonymousUser(), id="no-props"),
            pytest.param(
                _PROPS + "component.login_required()\n", AnonymousUser(), id="refused"
            ),
        ],
    )
    def test_no_token_without_props_or_a_passed_rule(
        self, site: _Site, module: str, user: object
    ) -> None:
        assert "data-next-token" not in self._failed(site, module, user)

    def test_a_component_naming_no_props_retries_on_its_own(self, site: _Site) -> None:
        html = self._failed(site, _HEAD + "component.props()\n", AnonymousUser())
        assert "data-next-token=" in html

    def test_a_stream_component_naming_no_props_retries_on_its_own(
        self, site: _Site
    ) -> None:
        site.add(
            "card",
            "{{ boom }}",
            error="E",
            loading="L",
            module=_HEAD + "component.props()\n",
        )
        html = site.finished("{% component 'card' %}", request=_request(None))
        assert 'data-next-state="error"' in html
        assert "data-next-token=" in html

    def test_no_token_without_a_sink_or_a_request(self, site: _Site) -> None:
        slot = SimpleNamespace(peek=lambda: None)
        with patch("next.components.boundary.deferrals_slot", slot):
            html = self._failed(site, _PROPS, AnonymousUser())
        assert "data-next-token" not in html
        site.add("bare", "<b>{{ x }}{{ boom }}</b>", error="E", module=_PROPS)
        assert "data-next-token" not in site.render("{% component 'bare' x=1 %}")


class _Admin:
    is_authenticated = True
    is_active = True

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return True


class _Recorder:
    """A sink that keeps the token input of every client-trip call it is handed."""

    def __init__(self) -> None:
        self.trips: list[TripInput | None] = []

    def queue(self, context: Context, call: DeferredCall) -> str:
        self.trips.append(call.renderer.trip_input())
        return ""


class TestEnclosingPermissions:
    """A call records the literal permissions of the granting branches around it."""

    @pytest.mark.parametrize(
        ("source", "perms"),
        [
            pytest.param("{% component 'card' %}", (), id="unguarded"),
            pytest.param(
                "{% has_perm 'a.one' %}{% has_perm 'a.two' %}{% has_perm 'a.one' %}"
                "{% component 'card' %}{% endhas_perm %}{% endhas_perm %}"
                "{% endhas_perm %}",
                ("a.one", "a.two"),
                id="literals",
            ),
            pytest.param(
                "{% has_perm 'a.one' %}{% else %}{% component 'card' %}{% endhas_perm %}",
                (),
                id="denying-branch",
            ),
            pytest.param(
                "{% has_perm 'a.one' %}{% endhas_perm %}{% component 'card' %}",
                (),
                id="closed-guard",
            ),
            pytest.param(
                "{% has_perm name %}{% component 'card' %}{% endhas_perm %}",
                None,
                id="variable",
            ),
            pytest.param(
                "{% has_perm 'a.one' %}{% has_perm 'a.two' row %}{% component 'card' %}"
                "{% endhas_perm %}{% endhas_perm %}",
                None,
                id="object",
            ),
        ],
    )
    def test_the_guards_are_read_as_the_call_compiles(
        self, source: str, perms: tuple[str, ...] | None
    ) -> None:
        (node,) = Template(source).nodelist.get_nodes_by_type(ComponentNode)
        assert node.perms == perms

    def test_a_guard_around_a_nested_block_of_a_child_template_is_claimed(
        self, site: _Site
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        engine = Engine(
            loaders=[
                (
                    "django.template.loaders.locmem.Loader",
                    {
                        "base.html": "{% block a %}{% endblock %}",
                        "page.html": "{% extends 'base.html' %}{% block a %}"
                        "{% has_perm 'shop.view_cart' %}<ul>{% block b %}"
                        "{% component 'card' x='1' %}{% endblock %}</ul>"
                        "{% endhas_perm %}{% endblock %}",
                    },
                )
            ],
            builtins=["next.templatetags.components", "next.templatetags.permissions"],
        )
        sink = _Recorder()
        context = site.context(request=_request(_Admin()), **{DEFERRALS_KEY: sink})
        engine.get_template("page.html").render(Context(context))
        (trip,) = sink.trips
        assert trip is not None
        assert trip.perms == ("shop.view_cart",)

    def test_a_client_trip_under_an_unnamed_guard_is_refused(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        with pytest.raises(TemplateSyntaxError, match="literal permission"):
            site.render(
                "{% has_perm name %}{% component 'card' x='1' %}{% endhas_perm %}",
                name="a.one",
                request=_request(_Admin()),
                **{DEFERRALS_KEY: _Recorder()},
            )

    @pytest.mark.parametrize(
        ("guard", "minted"), [("'a.one'", True), ("name", False)], ids=str
    )
    def test_a_retry_under_an_unnamed_guard_mints_no_token(
        self, site: _Site, guard: str, *, minted: bool
    ) -> None:
        site.add("card", "<b>{{ x }}{{ boom }}</b>", error="E", module=_PROPS)
        html = site.render(
            f"{{% has_perm {guard} %}}{{% component 'card' x='1' %}}{{% endhas_perm %}}",
            name="a.one",
            request=_request(_Admin()),
        )
        assert 'data-next-state="error"' in html
        assert ("data-next-token=" in html) is minted


_GUARDED_TEMPLATES = {
    "base.html": "{% has_perm 'x.y' %}{% block body %}{% endblock %}{% endhas_perm %}",
    "child.html": (
        "{% extends 'base.html' %}{% block body %}{% component 'card' x='1' %}"
        "{% endblock %}"
    ),
    "scoped.html": (
        "{% has_perm 'x.y' row %}{% block body %}{% endblock %}{% endhas_perm %}"
    ),
    "scoped_child.html": (
        "{% extends 'scoped.html' %}{% block body %}{% component 'card' x='1' %}"
        "{% endblock %}"
    ),
    "part.html": "{% component 'card' x='1' %}",
    "including.html": (
        "{% has_perm 'x.y' %}{% include 'part.html' %}{% endhas_perm %}"
    ),
    "named.html": "{% has_perm name %}{% include 'part.html' %}{% endhas_perm %}",
}


class TestRenderedGuards:
    """A call claims the granting branches other templates render around it."""

    @pytest.fixture()
    def engine(self) -> Engine:
        return Engine(
            loaders=[("django.template.loaders.locmem.Loader", _GUARDED_TEMPLATES)],
            builtins=["next.templatetags.components", "next.templatetags.permissions"],
        )

    def _trips(self, site: _Site, engine: Engine, name: str) -> list[TripInput | None]:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        sink = _Recorder()
        context = site.context(
            request=_request(_Admin()), name="x.y", row=1, **{DEFERRALS_KEY: sink}
        )
        engine.get_template(name).render(Context(context))
        return sink.trips

    @pytest.mark.parametrize("name", ["child.html", "including.html"])
    def test_a_guard_of_another_template_is_claimed(
        self, site: _Site, engine: Engine, name: str
    ) -> None:
        (trip,) = self._trips(site, engine, name)
        assert trip is not None
        assert trip.perms == ("x.y",)

    @pytest.mark.parametrize("name", ["scoped_child.html", "named.html"])
    def test_an_unnamed_guard_of_another_template_refuses_a_client_trip(
        self, site: _Site, engine: Engine, name: str
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match="literal permission"):
            self._trips(site, engine, name)

    def test_a_guard_around_a_component_reaches_the_call_in_its_template(
        self, site: _Site
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        site.add("outer", "<o>{% component 'card' x='1' %}</o>")
        sink = _Recorder()
        site.render(
            "{% has_perm 'x.y' %}{% component 'outer' %}{% endhas_perm %}",
            request=_request(_Admin()),
            **{DEFERRALS_KEY: sink},
        )
        (trip,) = sink.trips
        assert trip is not None
        assert trip.perms == ("x.y",)

    def test_a_job_renders_under_the_guards_open_when_it_was_queued(
        self, site: _Site
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        site.add("outer", "<o>{% component 'card' x='1' %}</o>", loading="O")
        with patch.object(component_tags, "TripInput", wraps=TripInput) as minted:
            site.finished(
                "{% has_perm 'x.y' %}{% component 'outer' %}{% endhas_perm %}",
                request=_request(_Admin()),
            )
        assert [call.args[3] for call in minted.call_args_list] == [("x.y",)]

    def test_an_isolated_render_keeps_the_claimed_guards(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", loading="L", module=_TRIP)
        info = site.add("outer", "<o>{% component 'card' x='1' %}</o>")
        sink = _Recorder()
        trip = TripInput(info, DeferMode.LOAD, {}, ("x.y",))
        renderer = component_tags.isolated_renderer(info, site.root, trip)
        renderer.body(
            Context(site.context(request=_request(_Admin()), **{DEFERRALS_KEY: sink}))
        )
        (inner,) = sink.trips
        assert inner is not None
        assert inner.perms == ("x.y",)


@pytest.fixture()
def guarded_layout(tmp_path: Path) -> Iterator[Path]:
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "layout.djx").write_text(
        "<html>{% has_perm 'x.y' %}{% template %}{% endhas_perm %}</html>"
    )
    card = pages / "_components" / "card"
    card.mkdir(parents=True)
    (card / "component.djx").write_text("<b>{{ x }}</b>")
    (card / "loading.djx").write_text("<b>L</b>")
    (card / "component.py").write_text(_TRIP)
    write_page(pages, "home", "", body="{% component 'card' x='1' %}")
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        yield pages


@pytest.mark.django_db()
@pytest.mark.usefixtures("guarded_layout")
def test_a_guard_of_the_layout_around_the_page_slot_is_claimed() -> None:
    client = Client()
    client.force_login(User.objects.create_superuser("root", password="pw"))
    with patch.object(component_tags, "TripInput", wraps=TripInput) as minted:
        response = client.get("/home/")
    assert response.status_code == 200
    assert "data-next-token=" in response.content.decode()
    assert [call.args[3] for call in minted.call_args_list] == [("x.y",)]


_DEFAULTS = (
    _HEAD
    + 'component.props("x")\n'
    + '@component.context\ndef defaults():\n    return {"x": "context"}\n'
)


class TestKeylessContextAgainstProps:
    """A reload meets a keyless context the way the inline render of its call does."""

    def _reloaded(self, site: _Site, props: dict[str, object]) -> str:
        info = site.infos["card"]
        trip = TripInput(info, DeferMode.LOAD, props)
        renderer = component_tags.isolated_renderer(info, site.root, trip)
        return renderer.body(Context(site.context()))[0]

    def test_a_passed_prop_conflicts_on_both_paths(self, site: _Site) -> None:
        site.add("card", "<b>{{ x }}</b>", error="E", module=_DEFAULTS)
        with boundary_errors("render"), recorded_failures() as failures:
            inline = site.render("{% component 'card' x=1 %}")
            reloaded = self._reloaded(site, {"x": 1})
        assert "E" in inline
        assert reloaded == "E"
        assert [type(failure.exception) for failure in failures] == [
            ComponentContextConflictError,
            ComponentContextConflictError,
        ]

    def test_an_unpassed_prop_takes_the_context_on_both_paths(
        self, site: _Site
    ) -> None:
        site.add("card", "<b>{{ x }}</b>", module=_DEFAULTS)
        assert site.render("{% component 'card' %}") == "<b>context</b>"
        assert self._reloaded(site, {}) == "<b>context</b>"


class TestTripRenderer:
    """A trip renderer names its token input and, after a failure, its retry."""

    def _renderer(self, site: _Site, **kwargs: object):
        info = site.add("card", "C", module=_TRIP)
        trip = TripInput(info, DeferMode.STREAM, {"x": 1})
        return component_tags.isolated_renderer(info, site.root, trip), kwargs

    @pytest.mark.parametrize(
        "failure",
        [
            BoundaryError(ErrorKind.ERROR, "d", None, retry=False),
            BoundaryError(ErrorKind.FORBIDDEN, "d", 403, retry=True),
        ],
    )
    def test_no_retry_without_a_script_or_after_a_refusal(
        self, site: _Site, failure: BoundaryError
    ) -> None:
        renderer, _ = self._renderer(site)
        assert renderer.retry_input(failure) is None

    def test_a_stream_input_is_no_client_trip(self, site: _Site) -> None:
        renderer, _ = self._renderer(site)
        assert renderer.trip_input() is None

    def test_a_rule_that_never_passed_offers_no_retry(self, site: _Site) -> None:
        info = site.add("card", "C", module=_LOGIN)
        node = ComponentNode("card", {}, NodeList())
        renderer = component_tags._DeferredRender(
            node, info, site.root, {}, granted=False, tag="div", trip=None
        )
        failure = BoundaryError(ErrorKind.ERROR, "d", None, retry=True)
        assert renderer.retry_input(failure) is None

    def test_a_declared_tag_wraps_an_isolated_render(self, site: _Site) -> None:
        module = _HEAD + 'component.defer("load", props=("x",), tag="section")\n'
        info = site.add("card", "C", module=module)
        trip = TripInput(info, DeferMode.LOAD, {"x": 1})
        renderer = component_tags.isolated_renderer(info, site.root, trip)
        assert renderer._tag == "section"
        assert renderer.trip_input() is trip
