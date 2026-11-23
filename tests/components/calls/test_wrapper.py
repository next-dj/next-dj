from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from next.diagnostics import degraded, watch_degraded
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE
from next.suspense.assembly import finish
from next.suspense.components import deferring
from tests.components.sites import FIRST_KEY, HEAD, TRIP, ComponentSite
from tests.support.boundaries import run_sequentially


def _bundle_url(name: str) -> str:
    return f"/static/next/next.{name}.min.js"


class TestWrapper:
    """The shell holds the loading wrapper, the assembled page its settled form."""

    def test_the_loading_wrapper_exactly(self, site: ComponentSite) -> None:
        site.add("card", "<c>C</c>", loading="L")
        shell, session = site.deferred("{% component 'card' %}")
        ident = session.jobs[0].id
        assert shell == (
            f'<!--{ident}:div--><div id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{FIRST_KEY}" data-next-state="loading" '
            f'aria-busy="true">L</div><!--/{ident}-->'
        )
        assert finish(shell, session, site.collector, run_sequentially) == (
            f'<div id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{FIRST_KEY}" data-next-state="ready"><c>C</c></div>'
        )

    def test_the_id_follows_the_session(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        shell, session = site.deferred("{% component 'card' %}{% component 'card' %}")
        assert [job.id for job in session.jobs] == [
            f"b-{session.seal}-0",
            f"b-{session.seal}-1",
        ]
        assert "card" not in shell

    def test_an_adopted_root_carries_the_boundary(self, site: ComponentSite) -> None:
        site.add("row", "<tr><td>{{ n }}</td></tr>", loading="<tr><td>.</td></tr>")
        shell, session = site.deferred("<table>{% component 'row' n=1 %}</table>")
        ident = session.jobs[0].id
        assert shell == (
            f'<table><!--{ident}:--><tr id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{FIRST_KEY}" data-next-state="loading" '
            f'aria-busy="true"><td>.</td></tr><!--/{ident}--></table>'
        )
        assert finish(shell, session, site.collector, run_sequentially) == (
            f'<table><tr id="{ident}" data-next-boundary="{ident}" '
            f'data-next-boundary-key="{FIRST_KEY}" data-next-state="ready"><td>1</td>'
            "</tr></table>"
        )

    def test_debug_names_the_owning_file(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            shell, _ = site.deferred("{% component 'card' %}")
        assert 'data-next-boundary-source="card/loading.djx"' in shell

    def test_debug_names_the_module_without_a_boundary_file(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C", module=HEAD)
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            shell, _ = site.deferred("{% component 'card' defer='stream' %}")
        assert 'data-next-boundary-source="card/component.py"' in shell


class TestLoadingStash:
    """An inline call inside a zone reloading to loading stashes its loading UI."""

    _LOOP = "{% for n in rows %}{% component 'card' defer='inline' %}{% endfor %}"

    def test_a_loading_zone_stashes_each_key_once(self, site: ComponentSite) -> None:
        site.add("card", "<c>C</c>", loading="<i>{{ who }}</i>")
        html = site.render(
            '{% zone "z" reload="loading" %}' + self._LOOP + "{% endzone %}",
            rows=[1, 2],
            who="w",
        )
        stash = f'<template data-next-loading="{FIRST_KEY}"><i>w</i></template>'
        assert html.count(stash) == 1
        assert html.count('data-next-state="ready"') == 2

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param('{% zone "z" %}' + _LOOP + "{% endzone %}", id="keep-zone"),
            pytest.param(_LOOP, id="no-zone"),
        ],
    )
    def test_elsewhere_nothing_is_stashed(
        self, site: ComponentSite, source: str
    ) -> None:
        site.add("card", "<c>C</c>", loading="<i>L</i>")
        assert "data-next-loading" not in site.render(source, rows=[1])

    def test_a_call_without_loading_ui_stashes_nothing(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>C</c>")
        html = site.render(
            '{% zone "z" reload="loading" %}' + self._LOOP + "{% endzone %}", rows=[1]
        )
        assert "data-next-loading" not in html


class TestLoading:
    """The loading UI reads the call site and the props, a failure its error UI."""

    def test_the_call_site_and_the_props(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="{{ page }}|{{ props.title }}|{{ title }}")
        shell, _ = site.deferred("{% component 'card' title='T' %}", page="P")
        assert ">P|T|</div>" in shell

    def test_without_a_file_or_a_default_the_wrapper_is_empty(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C")
        shell, _ = site.deferred("{% component 'card' defer='stream' %}")
        assert 'aria-busy="true"></div>' in shell

    def test_an_adopted_root_without_loading_ui_is_a_template(
        self, site: ComponentSite
    ) -> None:
        site.add("row", "<tr></tr>")
        shell, session = site.deferred(
            "<table>{% component 'row' defer='stream' %}</table>"
        )
        ident = session.jobs[0].id
        assert f'<template id="{ident}"' in shell

    def test_a_broken_loading_file_falls_to_the_error_ui(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C", loading="{{ boom }}", error="E:{{ error.kind }}")
        shell, session = site.deferred("{% component 'card' %}")
        assert 'aria-busy="true">E:error</div>' in shell
        assert "data-next-error" not in shell
        html = finish(shell, session, site.collector, run_sequentially)
        assert html.endswith('data-next-state="ready">C</div>')

    def test_the_project_default_stands_in(self, site: ComponentSite) -> None:
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


class TestShape:
    """A site adopting its root takes one root element, else it settles as an error."""

    def test_several_rows_settle_as_an_error(
        self, site: ComponentSite, caplog: pytest.LogCaptureFixture
    ) -> None:
        site.add("rows", "<li>a</li><li>b</li>", loading="<li>.</li>")
        watch_degraded()
        with caplog.at_level("ERROR", logger="next.components.calls"):
            html = site.finished("<ul>{% component 'rows' %}</ul>")
        assert html.startswith("<ul><template id=")
        assert html.endswith(
            'data-next-state="error" data-next-error="error"></template></ul>'
        )
        assert "renders 2 root elements" in caplog.text
        assert "digest" in caplog.text
        assert degraded() is True

    def test_debug_names_the_template_and_the_line(self, site: ComponentSite) -> None:
        site.add("rows", "loose", loading="<li>.</li>")
        with override_settings(DEBUG=True):
            html = site.finished("<ul>\n{% component 'rows' %}</ul>")
        assert "data-next-error-digest=" in html
        assert (
            'data-next-error-detail="the call of component &#x27;rows&#x27; in '
            "&lt;unknown source&gt;, line 2 renders loose text"
        ) in html

    def test_a_misfit_loading_ui_settles_the_shell_as_an_error(
        self, site: ComponentSite
    ) -> None:
        site.add("rows", "<li>a</li>", loading="<li>1</li><li>2</li>")
        shell, session = site.deferred("<ul>{% component 'rows' %}</ul>")
        assert 'data-next-state="error"' in shell
        assert "aria-busy" not in shell
        html = finish(shell, session, site.collector, run_sequentially)
        assert html == (
            f'<ul><li id="{session.jobs[0].id}" '
            f'data-next-boundary="{session.jobs[0].id}" '
            f'data-next-boundary-key="{FIRST_KEY}" data-next-state="ready">a</li></ul>'
        )

    def test_an_inline_call_settles_a_misfit_too(self, site: ComponentSite) -> None:
        site.add("rows", "<li>a</li><li>b</li>", loading="<li>.</li>")
        html = site.render("<ul>{% component 'rows' defer='inline' %}</ul>")
        assert 'data-next-state="error"' in html

    def test_an_empty_body_stays_a_ready_placeholder(self, site: ComponentSite) -> None:
        site.add("rows", "", loading="<li>.</li>")
        html = site.finished("<ul>{% component 'rows' %}</ul>")
        assert html.endswith('data-next-state="ready"></template></ul>')


class TestInsideLoading:
    """A deferred call inside a loading UI renders in place, with no job."""

    def test_the_spinner_renders_bare(self, site: ComponentSite) -> None:
        site.add("spin", "<s>S</s>", loading="never")
        site.add("card", "C", loading="{% component 'spin' %}")
        shell, session = site.deferred("{% component 'card' %}")
        assert len(session.jobs) == 1
        assert 'aria-busy="true"><s>S</s></div>' in shell
        assert "never" not in shell

    def test_a_client_trip_call_renders_bare(
        self, site: ComponentSite, request_for
    ) -> None:
        site.collector = StaticCollector(bundle_url=_bundle_url)
        site.add("trip", "<b>{{ x }}</b>", loading="never", module=TRIP)
        site.add("card", "C", loading="{% component 'trip' x=1 %}")
        with deferring(request=request_for(AnonymousUser())) as held:
            shell = site.render("{% component 'card' %}")
        assert held.session is not None
        assert len(held.session.jobs) == 1
        assert "<b>1</b>" in shell
        assert "data-next-token" not in shell
        assert "never" not in shell
        assert site.collector.notes(BUNDLE_NOTE) == ()

    def test_a_zone_skeleton_renders_its_calls_bare(
        self, site: ComponentSite, request_for
    ) -> None:
        site.collector = StaticCollector(bundle_url=_bundle_url)
        site.add("trip", "<b>{{ x }}</b>", loading="never", module=TRIP)
        site.add("slow", "<s>S</s>", loading="never")
        source = '{% zone "rows" reload="loading" %}<p>r</p>{% endzone %}'
        page = site.root / "list"
        page.mkdir()
        (page / "template.djx").write_text(source)
        (page / "loading.rows.djx").write_text(
            "{% component 'trip' x=1 %}{% component 'slow' defer='stream' %}"
        )
        with deferring(request=request_for(AnonymousUser())) as held:
            shell = site.render(
                source, current_template_path=str(page / "template.djx")
            )
        assert '<template data-next-loading="rows"><b>1</b><s>S</s></template>' in (
            shell
        )
        assert not (held.session and held.session.jobs)
        assert "data-next-token" not in shell
        assert site.collector.notes(BUNDLE_NOTE) == [_bundle_url("reload")]

    def test_a_stashed_loading_ui_renders_its_calls_bare(
        self, site: ComponentSite, request_for
    ) -> None:
        site.collector = StaticCollector(bundle_url=_bundle_url)
        site.add("trip", "<b>{{ x }}</b>", loading="never", module=TRIP)
        site.add("card", "<c>C</c>", loading="{% component 'trip' x=1 %}")
        with deferring(request=request_for(AnonymousUser())):
            html = site.render(
                '{% zone "z" reload="loading" %}'
                "{% component 'card' defer='inline' %}{% endzone %}"
            )
        assert f'<template data-next-loading="{FIRST_KEY}"><b>1</b></template>' in html
        assert "data-next-token" not in html
        assert site.collector.notes(BUNDLE_NOTE) == [_bundle_url("reload")]


class TestDocumentOrder:
    """The shell takes a queued call's co-located assets where the call stands."""

    def test_a_deferred_call_keeps_its_place_in_the_cascade(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "C", loading="L")
        site.add("plain", "P")
        site.finished("{% component 'card' %}{% component 'plain' %}")
        assert site.styles() == ["/card.css", "/plain.css"]
