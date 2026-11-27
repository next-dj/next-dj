from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Template
from django.utils.safestring import SafeString

import next.pages.boundaries as page_boundaries
from next.components import (
    ComponentInfo,
    components_manager,
    registry as registry_module,
)
from next.components.info import BoundaryFiles
from next.components.registry import DeferredComponents, ErrorFiles
from next.introspect import DeclaringSite
from next.pages import page
from next.pages.loaders import load_python_module_memo
from next.pages.manager import Page
from next.pages.signals import page_rendered
from next.pages.transactions import transaction_declaration
from next.partial.headers import CONTENT_TYPE, ORIGIN, VERSION
from next.requests import RequestKind
from next.router.links import TemplateLinks
from next.testing import capture_signals, envelope_of, override_next_settings
from next.transactions import AtomicScope, lifecycle as transactions_lifecycle
from next.views import plan as plan_module
from next.views.plan import PagePlans, inert_markup, reach, spells_container
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    partial_meta,
    touch_later,
    unified_view,
    write_page,
)
from tests.support.partial_requests import wsgi_header
from tests.support.views import write_dynamic_page


_LINKS = TemplateLinks()
_ZONED = 'a {% zone "z" %}<p>zoned</p>{% endzone %} b'
_NAV = '<nav><a href="/">Home</a></nav>'
_TEXT = "<p>text</p>"
LOADS = "{% load components %}"
_DOCUMENT = (
    "<!DOCTYPE html><html><head>{% collect_styles %}</head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
_TITLED = (
    "from next.pages import context\n\n\n"
    "@context('title')\ndef title():\n    return 'Hi'\n"
)
_TITLED_BODY = "<nav><a href='/'>Home</a></nav><h1>{{ title }}</h1>{{ request.path }}"


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


class TestInertMarkup:
    """A template is inert while every node writes only what its source spells."""

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
    def test_inert_nodes_keep_it_inert(self, source: str) -> None:
        assert inert_markup(Template(source)) is True

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
        assert inert_markup(Template(source)) is False


class TestSpelledContainer:
    """An inert template opens a container only where nothing but its source writes."""

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
        assert (plan.marks, plan.defers, plan.guarded, plan.awaits) == (
            False,
            False,
            False,
            False,
        )
        assert plan.links is True
        points = plan.points(RequestKind.PAGE, safe=True)
        assert (points.around, points.respond, points.finish) == ((), None, ())

    def test_the_plan_is_held_between_requests(self, page_instance, tmp_path) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        assert plans.of(template) is plans.of(template)

    def test_a_settings_override_rebuilds_it(self, page_instance, tmp_path) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        before = plans.of(template)
        with override_next_settings(ROUTER={"ENABLED": True}):
            routed = plans.of(template)
            assert len(routed.points(RequestKind.PAGE, safe=True).finish) == 1
            assert routed.plain is True
        after = plans.of(template)
        assert after.points(RequestKind.PAGE, safe=True).finish == ()
        assert after is not before

    def test_the_link_pass_switched_off_leaves_no_backend(
        self, page_instance, tmp_path
    ) -> None:
        plans, template = _planned(
            page_instance, build_nested_page(tmp_path, body=_NAV)
        )
        assert plans.of(template).marks is True
        with override_next_settings(ROUTER={"ACTIVE_LINKS": False}):
            plan = plans.of(template)
        assert plan.links is False
        assert plan.marks is False

    @pytest.mark.django_db()
    def test_a_safe_transaction_rule_rebuilds_it(self, page_instance, tmp_path) -> None:
        plans, template = _planned(page_instance, build_nested_page(tmp_path))
        assert plans.of(template).points(RequestKind.PAGE, safe=True).around == ()
        with override_next_settings(TRANSACTIONS={"SAFE": True}):
            plan = plans.of(template)
            assert len(plan.points(RequestKind.PAGE, safe=True).around) == 1
        assert plan.plain is True

    @pytest.mark.django_db()
    @pytest.mark.usefixtures("fresh_transaction_declarations")
    def test_a_registry_version_bump_rebuilds_it(self, page_instance, tmp_path) -> None:
        page_file = build_nested_page(tmp_path)
        plans, template = _planned(page_instance, page_file)
        before = plans.of(template)
        namespace = vars(load_python_module_memo(page_file))
        transaction_declaration.declare(
            DeclaringSite(page_file, True, namespace), atomic=True
        )
        after = plans.of(template)
        assert before.points(RequestKind.PAGE, safe=True).around == ()
        assert len(after.points(RequestKind.PAGE, safe=True).around) == 1
        assert after.plain is True

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

    def test_an_inert_page_ignores_what_the_project_defers(
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
        with override_next_settings(ROUTER={"ACTIVE_LINKS": False}):
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

    def test_a_straight_render_writes_what_the_full_render_writes(
        self, tmp_path
    ) -> None:
        (tmp_path / "layout.djx").write_text(_DOCUMENT)
        page_file = write_page(tmp_path, "leaf", _TITLED, body=_TITLED_BODY)
        load_python_module_memo(page_file)
        template = page.composed_template_for(page_file)
        straight = page.render_plain(
            page_file, template, build_page_request(), {}, dep_cache={}
        )
        whole = page.render(page_file, build_page_request(), {}, dep_cache={})
        assert "<h1>Hi</h1>" in straight
        assert "next.min.js" in straight
        assert straight == whole

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

    @pytest.mark.parametrize(
        ("link_pass", "marked"),
        [
            pytest.param("template", False, id="compiled"),
            pytest.param("document", True, id="document"),
        ],
    )
    def test_a_container_a_safe_variable_holds_is_marked_by_the_document_pass(
        self, page_instance, tmp_path, link_pass, *, marked
    ) -> None:
        page_file = build_nested_page(tmp_path, body="{{ menu }}")
        page_instance._context_manager.register_context(
            page_file, "menu", lambda: SafeString(_NAV)
        )
        request = build_page_request()
        request.path = "/"
        with override_next_settings(ROUTER={"ACTIVE_LINKS": {"PASS": link_pass}}):
            response = unified_view(page_instance, page_file)(request)
        assert (b'aria-current="page"' in response.content) is marked

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
        threaded = build_page_request()
        threaded.META["wsgi.multithread"] = True
        after = view(threaded).content
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
        with override_next_settings(ROUTER={"ACTIVE_LINKS": False}):
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
@pytest.mark.usefixtures(
    "quiet", "fresh_transaction_declarations", "watched_template_edits"
)
class TestPlanInTheDevLoop:
    """An edit the dev loop picks up reaches the plan on the request after it."""

    def test_an_edited_page_module_rebuilds_the_plan(
        self, page_instance, tmp_path, monkeypatch
    ) -> None:
        page_file = build_nested_page(tmp_path, body="<p>plain</p>")
        view = unified_view(page_instance, page_file)
        opened: list[tuple[str, ...]] = []
        original = transactions_lifecycle.request_atomic

        def recording(scope: AtomicScope):
            opened.append(scope.aliases)
            return original(scope)

        monkeypatch.setattr(transactions_lifecycle, "request_atomic", recording)
        view(build_page_request())
        touch_later(
            page_file, "from next.pages import page\n\npage.transaction(atomic=True)\n"
        )
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


def _component(
    root: Path,
    name: str,
    source: str,
    *,
    loading: bool = False,
    error: bool = False,
    module: str | None = None,
) -> ComponentInfo:
    """Write one component folder under `root` and describe it."""
    folder = root / "_components" / name
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text(source)
    module_path = None
    if module is not None:
        module_path = folder / "component.py"
        module_path.write_text(module)
    files = None
    if loading or error:
        files = BoundaryFiles(
            folder / "loading.djx" if loading else None,
            folder / "error.djx" if error else None,
        )
    return ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=folder / "component.djx",
        module_path=module_path,
        is_simple=False,
        files=files,
    )


def _page(root: Path, call: str) -> Path:
    """Write a nested page under `root` whose body is the component `call`."""
    root.mkdir()
    return build_nested_page(root, body=LOADS + call)


@contextmanager
def _visible(*infos: ComponentInfo) -> Iterator[None]:
    """Resolve each component name to its info, every other name to none."""
    by_name = {info.name: info for info in infos}
    with patch.object(
        components_manager,
        "get_component",
        side_effect=lambda name, _path: by_name.get(name),
    ):
        yield


@pytest.mark.usefixtures("quiet")
class TestComponentReach:
    """A page reads the components its template can call, through their own calls."""

    def test_a_plain_component_neither_defers_nor_fails(self, tmp_path) -> None:
        info = _component(tmp_path, "card", "<p>{{ title }}</p>")
        with _visible(info):
            found = reach(Template(LOADS + '{% component "card" %}'), tmp_path)
        assert found is not None
        assert (found.defers, found.guarded) == (False, False)

    def test_a_loading_ui_two_calls_deep_defers(self, tmp_path) -> None:
        outer = _component(tmp_path, "outer", LOADS + '{% component "inner" %}')
        inner = _component(tmp_path, "inner", "<p>x</p>", loading=True)
        with _visible(outer, inner):
            found = reach(Template(LOADS + '{% component "outer" %}'), tmp_path)
        assert found is not None
        assert found.defers is True

    def test_an_error_ui_guards(self, tmp_path) -> None:
        info = _component(tmp_path, "card", "<p>x</p>", error=True)
        with _visible(info):
            found = reach(Template(LOADS + '{% component "card" %}'), tmp_path)
        assert found is not None
        assert (found.defers, found.guarded) == (False, True)

    def test_a_call_site_mode_defers(self, tmp_path) -> None:
        info = _component(tmp_path, "card", "<p>x</p>")
        source = LOADS + '{% component "card" defer="stream" %}'
        with _visible(info):
            found = reach(Template(source), tmp_path)
        assert found is not None
        assert found.defers is True

    def test_the_metadata_of_a_layout_is_seen_through(self, tmp_path) -> None:
        info = _component(tmp_path, "card", "<p>x</p>")
        source = LOADS + '<head>{% metadata %}</head>{% component "card" %}'
        with _visible(info):
            assert reach(Template(source), tmp_path) is not None

    def test_an_unknown_name_is_left_out(self, tmp_path) -> None:
        with _visible():
            found = reach(Template(LOADS + '{% component "ghost" %}'), tmp_path)
        assert found is not None
        assert found.defers is False

    @pytest.mark.parametrize(
        "source",
        [
            "{% include 'card.html' %}",
            '{% zone "z" reload="loading" %}<p>row</p>{% endzone %}',
            "{% breadcrumbs %}",
        ],
    )
    def test_a_tag_of_unknown_output_is_blind(self, tmp_path, source) -> None:
        info = _component(tmp_path, "card", source)
        with _visible(info):
            assert reach(Template(LOADS + '{% component "card" %}'), tmp_path) is None

    def test_a_render_function_is_blind(self, tmp_path) -> None:
        info = _component(
            tmp_path, "card", "<p>x</p>", module="def render():\n    return ''\n"
        )
        with _visible(info):
            assert reach(Template(LOADS + '{% component "card" %}'), tmp_path) is None

    def test_lazy_modules_are_blind(self, tmp_path) -> None:
        with override_next_settings(LAZY_COMPONENT_MODULES=True):
            assert reach(Template("<p>x</p>"), tmp_path) is None

    def test_a_page_of_plain_components_stays_direct(
        self, page_instance, tmp_path, quiet
    ) -> None:
        deferred, guarded = quiet
        card = _component(tmp_path, "card", "<p>x</p>")
        slow = _component(tmp_path, "slow", "<p>x</p>", loading=True, error=True)
        plain = _page(tmp_path / "plain", '{% component "card" %}')
        late = _page(tmp_path / "late", '{% component "slow" %}')
        deferred.declare()
        guarded.note(object(), 1)
        with _visible(card, slow):
            plans, template = _planned(page_instance, plain)
            held = plans.of(template)
            slowed = PagePlans(page_instance, late).of(
                page_instance.composed_template_for(late)
            )
        assert (held.defers, held.guarded, held.plain) == (False, False, True)
        assert (slowed.defers, slowed.guarded, slowed.plain) == (True, True, False)


@pytest.mark.usefixtures("quiet", "watched_template_edits")
class TestComponentReachInTheDevLoop:
    """An edit to a component template reaches the plan of the page calling it."""

    def test_an_edited_component_template_rebuilds_the_plan(
        self, page_instance, tmp_path, quiet
    ) -> None:
        quiet[0].declare()
        card = _component(tmp_path, "card", "<p>x</p>")
        slow = _component(tmp_path, "slow", "<p>x</p>", loading=True)
        page_file = _page(tmp_path / "page", '{% component "card" %}')
        with _visible(card, slow):
            plans, template = _planned(page_instance, page_file)
            before = plans.of(template)
            assert plans.of(template) is before
            assert card.template_path is not None
            touch_later(card.template_path, LOADS + '{% component "slow" %}')
            after = plans.of(template)
        assert (before.defers, after.defers) == (False, True)

    def test_a_build_another_shape_interleaves_keeps_its_own_reach(
        self, page_instance, tmp_path, quiet
    ) -> None:
        quiet[0].declare()
        card = _component(tmp_path, "card", "<p>x</p>")
        slow = _component(tmp_path, "slow", "<p>x</p>", loading=True)
        page_file = _page(tmp_path / "page", '{% component "card" %}')
        chain = page_instance.layout_skeleton(page_file)[1]
        with _visible(card, slow):
            plans, template = _planned(page_instance, page_file)
            atomic = plan_module.page_transaction
            calls: list[Path] = []

            def between(file_path: Path) -> object:
                # The other shape builds whole while this build stands mid-way.
                calls.append(file_path)
                if len(calls) == 1:
                    plans.of(chain)
                return atomic(file_path)

            with patch.object(plan_module, "page_transaction", side_effect=between):
                before = plans.of(template)
            assert plans.of(template) is before
            assert card.template_path is not None
            touch_later(card.template_path, LOADS + '{% component "slow" %}')
            after = plans.of(template)
        assert (before.defers, after.defers) == (False, True)

    def test_a_removed_component_template_rebuilds_the_plan(
        self, page_instance, tmp_path, quiet
    ) -> None:
        quiet[0].declare()
        card = _component(tmp_path, "card", "<p>x</p>")
        page_file = _page(tmp_path / "page", '{% component "card" %}')
        with _visible(card):
            plans, template = _planned(page_instance, page_file)
            before = plans.of(template)
            assert card.template_path is not None
            card.template_path.unlink()
            assert plans.of(template) is not before

    def test_a_component_template_broken_since_is_read_again(
        self, page_instance, tmp_path, quiet
    ) -> None:
        card = _component(tmp_path, "card", "<p>x</p>")
        page_file = _page(tmp_path / "page", '{% component "card" %}')
        with _visible(card):
            plans, template = _planned(page_instance, page_file)
            before = plans.of(template)
            assert card.template_path is not None
            touch_later(card.template_path, "{% if %}")
            after = plans.of(template)
        assert (before.plain, after.layers) == (True, True)


@pytest.mark.usefixtures("quiet")
class TestLayerLinks:
    """A page scans its document for layer links only once a template spells one."""

    def test_an_inert_page_reads_its_source(self, page_instance, tmp_path) -> None:
        for name, body, expected in (
            ("plain", _TEXT, False),
            ("linked", '<a href="/x/" DATA-NEXT-LAYER="sheet">x</a>', True),
        ):
            (tmp_path / name).mkdir()
            page_file = build_nested_page(tmp_path / name, body=body)
            plans, template = _planned(page_instance, page_file)
            assert plans.of(template).layers is expected

    def test_a_component_template_that_spells_one(
        self, page_instance, tmp_path
    ) -> None:
        card = _component(tmp_path, "card", '<a data-next-layer="sheet">x</a>')
        with _visible(card):
            plans, template = _planned(
                page_instance, _page(tmp_path / "page", '{% component "card" %}')
            )
            assert plans.of(template).layers is True

    def test_a_component_template_without_one(self, page_instance, tmp_path) -> None:
        card = _component(tmp_path, "card", "<p>{{ link }}</p>")
        with _visible(card):
            plans, template = _planned(
                page_instance, _page(tmp_path / "page", '{% component "card" %}')
            )
            assert plans.of(template).layers is False

    @pytest.mark.parametrize("source", ["{% include 'x.html' %}", "{% if %}"])
    def test_a_blind_component_template_may_hold_one(
        self, page_instance, tmp_path, source
    ) -> None:
        card = _component(tmp_path, "card", source)
        with _visible(card):
            plans, template = _planned(
                page_instance, _page(tmp_path / "page", '{% component "card" %}')
            )
            assert plans.of(template).layers is True

    def test_a_render_body_may_hold_one(self, page_instance, tmp_path) -> None:
        page_file = write_dynamic_page(tmp_path / "dynamic")
        chain = page_instance.layout_skeleton(page_file)[1]
        assert PagePlans(page_instance, page_file).of(chain).layers is True
