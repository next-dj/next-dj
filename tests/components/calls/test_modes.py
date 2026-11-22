from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.template import Context
from django.template.base import NodeList
from django.test import override_settings

from next.components import ComponentInfo
from next.defer import DEFERRALS_KEY, BoundaryOptions, DeferMode, stable_key
from next.suspense.assembly import finish, run_sequentially
from next.suspense.components import deferring
from next.templatetags.components import ComponentNode
from tests.components.sites import FIRST_KEY, HEAD, ComponentSite


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
        site: ComponentSite,
        loading: str | None,
        declared: str | None,
        tag: str,
        jobs: int | None,
        state: str | None,
    ) -> None:
        site.add("card", "<c>C</c>", loading=loading, module=HEAD)
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
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C")
        _, session = site.deferred("{% component 'card' defer='manual' timeout=2 %}")
        job = session.jobs[0]
        assert job.site.mode is DeferMode.STREAM
        assert job.options.mode is DeferMode.STREAM
        assert job.options.timeout == pytest.approx(2.0)

    def test_the_call_site_timeout_wins_over_the_module(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C", module=HEAD)
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

    def test_the_options_never_reach_the_props(self, site: ComponentSite) -> None:
        site.add("card", "[{{ defer }}{{ timeout }}]")
        assert site.render("{% component 'card' defer='inline' timeout=1 %}").endswith(
            "[]</div>"
        )


class TestZeroCost:
    """A component with no boundary file and no declaration never reads a sink."""

    def test_a_plain_render_touches_no_sink(self, site: ComponentSite) -> None:
        site.add("plain", "<p>{{ x }}</p>")
        sink = Mock()
        slot = Mock()
        with patch("next.components.boundaries.deferrals_slot", slot):
            html = site.render("{% component 'plain' x=1 %}", **{DEFERRALS_KEY: sink})
        assert html == "<p>1</p>"
        assert sink.mock_calls == []
        assert slot.mock_calls == []

    def test_an_error_boundary_alone_takes_the_guarded_path(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "{{ boom }}", error="E")
        slot = Mock()
        with patch("next.components.boundaries.deferrals_slot", slot):
            html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert slot.mock_calls == []


class TestSink:
    """The context sink comes first, then the port, then a render in place."""

    def test_the_context_sink_takes_the_call(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        sink = Mock()
        sink.queue.return_value = "queued"
        assert (
            site.render("{% component 'card' %}", **{DEFERRALS_KEY: sink}) == "queued"
        )

    def test_without_any_sink_the_call_renders_behind_its_boundary(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "{{ boom }}", loading="L", error="E")
        slot = Mock()
        slot.peek.return_value = None
        with patch("next.components.boundaries.deferrals_slot", slot):
            html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert html.endswith('data-next-error="error">E</div>')

    def test_outside_a_deferring_block_the_call_renders_in_place(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>C</c>", loading="L")
        html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert html.endswith(
            f'data-next-boundary-key="{FIRST_KEY}" data-next-state="ready"><c>C</c></div>'
        )

    def test_failing_props_render_in_place_behind_the_boundary(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C", loading="L", error="E")
        with deferring() as held:
            html = site.render("{% component 'card' title=boom %}")
        assert held.session is None
        assert html.endswith('data-next-error="error">E</div>')


class TestDeclaredWrapperTag:
    """A declared tag overrides the markup before the call."""

    @pytest.mark.parametrize(
        ("tag", "opening"), [("section", "<section id="), (None, "<p id=")]
    )
    def test_the_declared_tag_wraps(
        self, site: ComponentSite, tag: str | None, opening: str
    ) -> None:
        site.add("card", "C", loading="<p>L</p>", module=HEAD)
        store = SimpleNamespace(
            options=lambda _path: BoundaryOptions.parse("stream", tag=tag)
        )
        with patch("next.templatetags.components.defer_declarations", store):
            shell, _ = site.deferred("{% component 'card' %}")
        assert opening in shell


class TestDetached:
    """A node rendered outside any template still defers."""

    def test_a_bare_node_queues_and_runs(self, site: ComponentSite) -> None:
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
        self, site: ComponentSite
    ) -> None:
        site.infos["ghost"] = ComponentInfo(
            "ghost", site.root, "", None, None, is_simple=True
        )
        with override_settings(DEBUG=True):
            shell, _ = site.deferred("{% component 'ghost' defer='stream' %}")
        assert "data-next-boundary-source" not in shell
