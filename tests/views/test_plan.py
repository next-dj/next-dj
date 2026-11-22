from pathlib import Path

import pytest
from django.template import Template
from django.utils.safestring import SafeString

import next.pages.boundaries as page_boundaries
from next.components import registry as registry_module
from next.components.registry import DeferredComponents, ErrorFiles
from next.pages.loaders import load_python_module_memo
from next.pages.manager import Page
from next.pages.signals import page_rendered
from next.pages.transactions import AtomicDeclaration, atomic_store
from next.partial.headers import CONTENT_TYPE, ORIGIN, VERSION
from next.router import CurrentLinks
from next.suspense.policy import streaming
from next.testing import capture_signals, envelope_of, override_next_settings
from next.transactions import AtomicScope
from next.views import pages as view_pages
from next.views.plan import PagePlans, sealed, spells_container
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    partial_meta,
    touch_later,
    unified_view,
)
from tests.support.partial_requests import wsgi_header
from tests.support.views import write_dynamic_page


_LINKS = CurrentLinks()
_ZONED = 'a {% zone "z" %}<p>zoned</p>{% endzone %} b'
_NAV = '<nav><a href="/">Home</a></nav>'
_TEXT = "<p>text</p>"


@pytest.fixture()
def quiet(
    fresh_deferred_components: DeferredComponents, monkeypatch: pytest.MonkeyPatch
) -> tuple[DeferredComponents, ErrorFiles]:
    """Start the project without a deferrable or a guarded component."""
    guarded = ErrorFiles()
    for module in (registry_module, page_boundaries):
        monkeypatch.setattr(module, "error_files", guarded)
    return fresh_deferred_components, guarded


def _planned(page: Page, page_file: Path) -> tuple[PagePlans, Template]:
    """Return the plans of `page_file` and the compiled template they are asked with."""
    return PagePlans(page, page_file), page.composed_template_for(page_file)


class TestSealedTemplate:
    """A template is sealed while every node writes only what its source spells."""

    @pytest.mark.parametrize(
        "source",
        [
            "<p>{{ title }}</p>",
            "{% if a %}<b>{{ a }}</b>{% else %}{{ b }}{% endif %}",
            "{% for x in xs %}{{ x }}{% endfor %}",
            '{% zone "z" %}<p>row</p>{% endzone %}',
            "<head>{% collect_styles %}</head>{% collect_scripts %}",
            '{% has_perm "auth.view_user" %}<p>in</p>{% endhas_perm %}',
            "{% #template %}<p>fallback</p>{% /template %}",
        ],
    )
    def test_inert_nodes_keep_it_sealed(self, source: str) -> None:
        assert sealed(Template(source)) is True

    @pytest.mark.parametrize(
        "source",
        [
            "{{ body|safe }}",
            "{% for x in xs %}{{ x|safeseq|join:', ' }}{% endfor %}",
            '{% zone "z" reload="loading" %}<p>row</p>{% endzone %}',
            "{% breadcrumbs %}",
            "{% now 'Y' as year %}{% firstof year %}",
            "{% csrf_token %}{% include 'missing.html' %}",
        ],
    )
    def test_a_node_of_unknown_output_opens_it(self, source: str) -> None:
        assert sealed(Template(source)) is False


class TestSpelledContainer:
    """A sealed template opens a container only where nothing but its source writes."""

    @pytest.mark.parametrize(
        "source",
        [
            _NAV,
            '<ul role="navigation"></ul>',
            "<div data-next-nav></div>",
            "<{% if wide %}nav{% else %}div{% endif %}>",
            "<NAV></NAV>",
            '{% zone "z" tag="nav" %}<p>row</p>{% endzone %}',
        ],
    )
    def test_a_container_the_source_spells(self, source: str) -> None:
        assert spells_container(Template(source), _LINKS) is True

    @pytest.mark.parametrize(
        "source",
        [
            "<main><h1>{{ title }}</h1></main>",
            "<{{ tag }}>",
            "<div {{ attrs }}>",
            "{% if on %}{{ menu }}{% endif %}",
            '{% now "Y" %}',
        ],
    )
    def test_a_node_that_writes_past_the_source_may_open_one(self, source: str) -> None:
        assert spells_container(Template(source), _LINKS) is True

    @pytest.mark.parametrize(
        "source",
        [
            "<main><h1>Title</h1></main>",
            '<a href="/" class="{% if on %}on{% endif %}">Home</a>',
            '{% zone "z" %}<p>row</p>{% endzone %}',
        ],
    )
    def test_a_source_without_one(self, source: str) -> None:
        assert spells_container(Template(source), _LINKS) is False


@pytest.mark.usefixtures("quiet")
class TestPagePlan:
    """The plan of a page is decided once and rebuilt when what it reads moves."""

    def test_a_page_without_features_is_plain(self, page_instance, tmp_path) -> None:
        page_file = build_nested_page(tmp_path, body=_TEXT)
        plans, template = _planned(page_instance, page_file)
        plan = plans.of(template)
        assert plan.plain is True
        assert (plan.router, plan.marks, plan.defers, plan.guarded) == (
            False,
            False,
            False,
            False,
        )
        assert plan.reads == ()
        assert plan.awaits is False
        assert isinstance(plan.links, CurrentLinks)

    def test_the_plan_is_held_between_requests(self, page_instance, tmp_path) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        assert plans.of(template) is plans.of(template)

    def test_a_settings_override_rebuilds_it(self, page_instance, tmp_path) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        before = plans.of(template)
        with override_next_settings(ROUTER={"ENABLED": True}):
            routed = plans.of(template)
            assert routed.router is True
            assert routed.plain is False
        after = plans.of(template)
        assert (after.router, after.plain) == (before.router, before.plain)
        assert after is not before

    def test_the_link_pass_switched_off_leaves_no_backend(
        self, page_instance, tmp_path
    ) -> None:
        plans, template = _planned(
            page_instance, build_nested_page(tmp_path, body=_NAV)
        )
        assert plans.of(template).marks is True
        with override_next_settings(ROUTER={"CURRENT": False}):
            plan = plans.of(template)
        assert plan.links is None
        assert plan.marks is False

    @pytest.mark.django_db()
    def test_a_safe_transaction_rule_rebuilds_it(self, page_instance, tmp_path) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        assert plans.of(template).reads == ()
        with override_next_settings(TRANSACTIONS={"SAFE": True}):
            plan = plans.of(template)
        assert plan.reads == ("default",)
        assert plan.plain is False

    @pytest.mark.django_db()
    @pytest.mark.usefixtures("fresh_atomic_declarations")
    def test_a_registry_version_bump_rebuilds_it(self, page_instance, tmp_path) -> None:
        page_file = build_nested_page(tmp_path)
        plans, template = _planned(page_instance, page_file)
        before = plans.of(template)
        namespace = vars(load_python_module_memo(page_file))
        atomic_store.declare(
            page_file, AtomicDeclaration(True, None, namespace, top_level=True)
        )
        after = plans.of(template)
        assert before.reads == ()
        assert after.reads == ("default",)
        assert after.plain is False

    def test_a_context_registered_later_rebuilds_it(
        self, page_instance, tmp_path
    ) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        before = plans.of(template)
        page_instance.epoch += 1
        assert plans.of(template) is not before

    def test_a_recomposed_template_rebuilds_it(self, page_instance, tmp_path) -> None:
        page_file = build_nested_page(tmp_path, body=_TEXT)
        plans, template = _planned(page_instance, page_file)
        before = plans.of(template)
        page_instance.clear_template_caches()
        (page_file.parent / "template.djx").write_text(_NAV)
        recomposed = page_instance.composed_template_for(page_file)
        assert recomposed is not template
        assert before.marks is False
        assert plans.of(recomposed).marks is True

    def test_a_sealed_page_ignores_what_the_project_defers(
        self, page_instance, tmp_path, quiet
    ) -> None:
        deferred, guarded = quiet
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        deferred.declare()
        guarded.note(object(), 1)
        plan = plans.of(template)
        assert (plan.defers, plan.guarded, plan.plain) == (False, False, True)
        assert plan.streams is True

    def test_an_open_page_follows_what_the_project_defers(
        self, page_instance, tmp_path, quiet
    ) -> None:
        deferred, guarded = quiet
        page_file = write_dynamic_page(tmp_path / "dynamic")
        plans = PagePlans(page_instance, page_file)
        chain = page_instance.layout_skeleton(page_file)[1]
        before = plans.of(chain)
        deferred.declare()
        deferring = plans.of(chain)
        guarded.note(object(), 1)
        guarding = plans.of(chain)
        assert (before.plain, before.marks) == (True, True)
        assert (deferring.defers, deferring.guarded) == (True, False)
        assert (guarding.defers, guarding.guarded) == (True, True)

    def test_a_render_body_has_no_backend_to_mark_with_the_pass_off(
        self, page_instance, tmp_path
    ) -> None:
        page_file = write_dynamic_page(tmp_path / "dynamic")
        plans = PagePlans(page_instance, page_file)
        chain = page_instance.layout_skeleton(page_file)[1]
        with override_next_settings(ROUTER={"CURRENT": False}):
            assert plans.of(chain).marks is False


@pytest.mark.usefixtures("quiet")
class TestPlainPage:
    """A page its plan finds plain renders outside every optional block."""

    def test_a_static_page_renders_through_its_layouts(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>{{ request.path }}plain</p>")
        response = unified_view(page_instance, page_file)(build_page_request())
        assert response.content == b"<html><main><p>plain</p></main></html>"
        assert "X-Next-Request" in response["Vary"]

    def test_a_plain_page_still_announces_its_render(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>plain</p>")
        view = unified_view(page_instance, page_file)
        with capture_signals(page_rendered) as recorder:
            view(build_page_request())
        (event,) = recorder.events
        assert event.kwargs["file_path"] == page_file

    def test_a_container_the_template_spells_is_marked(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_NAV)
        request = build_page_request()
        request.path = "/"
        response = unified_view(page_instance, page_file)(request)
        assert b'aria-current="page"' in response.content

    def test_a_container_a_safe_variable_holds_is_marked(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body="{{ menu }}")
        page_instance._context_manager.register_context(
            page_file, "menu", lambda: SafeString(_NAV)
        )
        request = build_page_request()
        request.path = "/"
        response = unified_view(page_instance, page_file)(request)
        assert b'aria-current="page"' in response.content

    def test_a_project_that_defers_marks_the_payload_of_a_plain_page(
        self, page_instance, tmp_path, quiet
    ) -> None:
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% template %}{% collect_scripts %}</body></html>"
        )
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        (tmp_path / "template.djx").write_text("<h1>plain</h1>")
        view = unified_view(page_instance, page_file)
        before = view(build_page_request()).content
        quiet[0].declare()
        with streaming():
            after = view(build_page_request()).content
        assert b'"$stream":true' not in before
        assert b'"$stream":true' in after

    def test_a_render_body_is_composed_and_marked(
        self, page_instance, tmp_path
    ) -> None:
        page_file = tmp_path / "dynamic" / "page.py"
        page_file.parent.mkdir()
        page_file.write_text(f"def render(request):\n    return {_NAV!r}\n")
        request = build_page_request()
        request.path = "/"
        response = unified_view(page_instance, page_file)(request)
        assert b'aria-current="page"' in response.content

    def test_a_render_body_that_answers_itself_is_passed_on(
        self, page_instance, tmp_path
    ) -> None:
        page_file = tmp_path / "dynamic" / "page.py"
        page_file.parent.mkdir()
        page_file.write_text(
            "from django.http import HttpResponse\n\n"
            "def render(request):\n    return HttpResponse('own', status=202)\n"
        )
        response = unified_view(page_instance, page_file)(build_page_request())
        assert (response.status_code, response.content) == (202, b"own")

    def test_an_empty_render_body_leaves_the_layout(
        self, page_instance, tmp_path
    ) -> None:
        (tmp_path / "layout.djx").write_text("<main>{% template %}</main>")
        page_file = tmp_path / "dynamic" / "page.py"
        page_file.parent.mkdir()
        page_file.write_text("def render(request):\n    return ''\n")
        response = unified_view(page_instance, page_file)(build_page_request())
        assert response.content == b"<main></main>"

    def test_the_token_field_leaves_the_query_of_a_plain_page(
        self, page_instance, tmp_path, rf
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>plain</p>")
        request = rf.get("/?a=1&_nb=secret")
        unified_view(page_instance, page_file)(request)
        assert request.META["QUERY_STRING"] == "a=1"
        assert "_nb" not in request.GET

    def test_a_request_without_a_query_parses_none(
        self, page_instance, tmp_path, rf
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>plain</p>")
        request = rf.get("/")
        unified_view(page_instance, page_file)(request)
        assert "GET" not in vars(request)


@pytest.mark.usefixtures("quiet")
class TestPlainZone:
    """A zone request of a plain page answers its envelope without a stream block."""

    def test_the_zone_is_patched_and_varied_on_the_origin(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_ZONED)
        response = unified_view(page_instance, page_file)(build_zone_request("z"))
        assert envelope_of(response).html_for_zone("z") == (
            '<div data-next-zone="z"><p>zoned</p></div>'
        )
        assert ORIGIN in response["Vary"]
        assert response["Cache-Control"] == "private, no-store"

    def test_the_pass_switched_off_drops_the_origin_from_vary(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_ZONED)
        view = unified_view(page_instance, page_file)
        with override_next_settings(ROUTER={"CURRENT": False}):
            response = view(build_zone_request("z"))
        assert ORIGIN not in response["Vary"]

    def test_a_zone_holding_a_container_is_marked(
        self, page_instance, tmp_path
    ) -> None:
        body = f'{{% zone "z" %}}{_NAV}{{% endzone %}}'
        page_file = build_nested_page(tmp_path, body=body)
        request = build_zone_request("z")
        request.path = "/"
        response = unified_view(page_instance, page_file)(request)
        assert 'aria-current="page"' in envelope_of(response).html_for_zone("z")

    def test_a_zone_request_without_a_query_parses_none(
        self, page_instance, tmp_path, rf
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_ZONED)
        request = rf.get("/", **partial_meta(zones="z"))
        assert unified_view(page_instance, page_file)(request).status_code == 200
        assert "GET" not in vars(request)

    def test_an_unknown_zone_is_refused_with_the_same_vary(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_ZONED)
        response = unified_view(page_instance, page_file)(build_zone_request("missing"))
        assert response.status_code == 400
        assert ORIGIN in response["Vary"]

    def test_a_stale_version_conflicts_with_the_same_vary(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=_ZONED)
        request = build_zone_request("z")
        request.META[wsgi_header(VERSION)] = "stale"
        response = unified_view(page_instance, page_file)(request)
        assert response.status_code == 409
        assert ORIGIN in response["Vary"]


@pytest.mark.usefixtures("quiet")
class TestNavigationOfAnUndeferredPage:
    """A navigation of a page that defers nothing gathers no deferred call."""

    def test_the_envelope_morphs_the_page(self, page_instance, tmp_path, rf) -> None:
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% template %}{% collect_scripts %}</body></html>"
        )
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        (tmp_path / "template.djx").write_text("<h1>plain</h1>")
        request = rf.get(
            "/",
            headers={
                "X-Next-Request": "1",
                "X-Next-Navigate": "1",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        with override_next_settings(ROUTER={"ENABLED": True}):
            response = unified_view(page_instance, page_file)(request)
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        assert b"<h1>plain</h1>" in response.content


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("quiet", "fresh_atomic_declarations", "watched_template_edits")
class TestPlanInTheDevLoop:
    """An edit the dev loop picks up reaches the plan on the request after it."""

    def test_an_edited_page_module_rebuilds_the_plan(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>plain</p>")
        view = unified_view(page_instance, page_file)
        opened: list[tuple[str, ...]] = []
        original = view_pages.request_atomic

        def recording(scope: AtomicScope):
            opened.append(scope.aliases)
            return original(scope)

        monkeypatch.setattr(view_pages, "request_atomic", recording)
        view(build_page_request())
        touch_later(page_file, "from next.pages import page\n\npage.atomic(True)\n")
        view(build_page_request())
        assert opened == [("default",)]

    def test_an_edited_template_rebuilds_the_plan(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>plain</p>")
        view = unified_view(page_instance, page_file)
        request = build_page_request()
        request.path = "/"
        assert b"aria-current" not in view(request).content
        touch_later(page_file.parent / "template.djx", _NAV)
        assert b'aria-current="page"' in view(request).content
