import gc
import os
import time
from pathlib import Path

import pytest
from django.http import HttpRequest
from django.template import Context, Engine
from django.template.base import Template
from django.test import RequestFactory

from next.pages import Page
from next.pages.loaders import _load_python_module_memo
from next.partial import zone_requested, zones_of
from next.partial.markers import ZONE_ATTR, ZoneNode
from next.partial.registry import _zone_cache


class _Boom:
    """A context value whose stringification raises at render time."""

    def __init__(self, message: str) -> None:
        self.message = message

    def __str__(self) -> str:
        raise ValueError(self.message)


def _debug_engine() -> Engine:
    return Engine(debug=True, builtins=["next.templatetags.partial"])


def _partial_of(source: str, engine: Engine) -> tuple[Template, ZoneNode]:
    template = Template(source, engine=engine)
    node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
    # the compile hook stores the default engine on the partial. Under a real
    # DEBUG deployment that engine has debug on. The suite mutates the default
    # engine, so pin the debug engine the partial stands for so the standalone
    # render reaches the same `render_annotated` debug branch production does.
    node.partial.engine = engine
    return template, node


class TestStandaloneDebugContract:
    """A standalone zone render keeps an honest DEBUG traceback.

    These cases drive `ZonePartial.render` through the branch where
    `context.template is None`, so `bind_template` puts the partial on
    `render_context.template` and its `get_exception_info` is the only
    delegate. The inline page-render case never reaches this branch.
    """

    def test_template_debug_during_through_bind_template(self) -> None:
        engine = _debug_engine()
        template, node = _partial_of(
            'head {% zone "z" %}<p>{{ bad }}</p>{% endzone %} tail', engine
        )
        partial = node.partial

        # a full render first binds the page template the partial delegates to
        template.render(Context({"bad": "ok"}))
        assert partial.page_template is not None

        with pytest.raises(ValueError, match="boom-standalone") as exc_info:
            partial.render(Context({"bad": _Boom("boom-standalone")}))

        debug = exc_info.value.template_debug  # type: ignore[attr-defined]
        assert debug["during"] == "{{ bad }}"

    def test_standalone_binds_template_name(self) -> None:
        engine = _debug_engine()
        _template, node = _partial_of(
            '{% zone "named" %}{{ request.path }}{% endzone %}', engine
        )
        context = Context({"request": RequestFactory().get("/")})
        assert node.partial.render(context) == "/"
        assert context.template_name == "named"


class TestUnboundZoneDebugDoesNotCrash:
    """A zone with no bound page template renders honestly under DEBUG.

    The render goes through the real `Node.render_annotated` path, so a
    body exception under `engine.debug` must not surface as the
    `AttributeError` that an undelegated `render_context.template` would
    raise. With no page template the debug info is simply empty.
    """

    def test_body_exception_is_raised_verbatim(self) -> None:
        engine = _debug_engine()
        _template, node = _partial_of(
            '{% zone "z" %}<p>{{ bad }}</p>{% endzone %}', engine
        )
        partial = node.partial
        assert partial.page_template is None

        with pytest.raises(ValueError, match="boom-unbound") as exc_info:
            partial.render(Context({"bad": _Boom("boom-unbound")}))

        # the honest error survives, no AttributeError masks it
        assert not isinstance(exc_info.value, AttributeError)

    def test_template_debug_is_empty_without_page_template(self) -> None:
        engine = _debug_engine()
        _template, node = _partial_of(
            '{% zone "z" %}<p>{{ bad }}</p>{% endzone %}', engine
        )
        with pytest.raises(ValueError, match="empty-debug") as exc_info:
            node.partial.render(Context({"bad": _Boom("empty-debug")}))
        assert exc_info.value.template_debug == {}  # type: ignore[attr-defined]


class TestWrapperTagContexts:
    """The `tag=` kwarg emits the wrapper element raw for its context."""

    def test_li_inside_ul(self) -> None:
        source = '<ul>{% zone "items" tag="li" %}<a>{{ x }}</a>{% endzone %}</ul>'
        out = Template(source).render(Context({"x": "q"}))
        assert out == f'<ul><li {ZONE_ATTR}="items"><a>q</a></li></ul>'

    def test_optgroup_inside_select(self) -> None:
        source = (
            '<select>{% zone "opts" tag="optgroup" %}'
            "<option>{{ x }}</option>{% endzone %}</select>"
        )
        out = Template(source).render(Context({"x": "q"}))
        assert out == (
            f'<select><optgroup {ZONE_ATTR}="opts"><option>q</option></optgroup></select>'
        )


class TestLazyZoneBodyIsInert:
    """A lazy zone emits only its placeholder, never the body."""

    @pytest.mark.parametrize("trigger", ["load", "revealed"])
    def test_heavy_body_var_absent_from_output(self, trigger: str) -> None:
        source = (
            f'{{% zone "z" lazy="{trigger}" %}}'
            "<table>{{ heavy }}</table>"
            "{% placeholder %}<div>loading</div>{% endzone %}"
        )
        out = Template(source).render(Context({"heavy": "MUST-NOT-APPEAR"}))
        assert "MUST-NOT-APPEAR" not in out
        assert "<table>" not in out
        assert "<div>loading</div>" in out


def _write_zone_page(directory: Path, body: str) -> Path:
    page_file = directory / "page.py"
    page_file.write_text("x = 1")
    (directory / "template.djx").write_text(body)
    return page_file


def _make_request() -> HttpRequest:
    request = HttpRequest()
    request.method = "GET"
    request.META["SERVER_NAME"] = "testserver"
    request.META["SERVER_PORT"] = "80"
    return request


class TestFullPageRenderEquivalence:
    """A composed page with a zone differs only by the zone wrapper."""

    def test_byte_for_byte_outside_the_wrapper(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        zoned_dir = tmp_path / "zoned"
        zoned_dir.mkdir()
        plain_page = _write_zone_page(plain_dir, "<main><p>{{ msg }}</p></main>")
        zoned_page = _write_zone_page(
            zoned_dir,
            '<main>{% zone "z" %}<p>{{ msg }}</p>{% endzone %}</main>',
        )

        plain = page_instance.render(plain_page, msg="hi")
        zoned = page_instance.render(zoned_page, msg="hi")
        opener = f'<div {ZONE_ATTR}="z">'
        assert zoned == plain.replace("<p>hi</p>", f"{opener}<p>hi</p></div>")


class TestRegistryIsolatedFromGetPath:
    """The per-request GET render never writes to the zone registry."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _zone_cache.clear()
        yield
        _zone_cache.clear()

    def test_unified_view_get_does_not_populate_cache(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        page_file = _write_zone_page(
            tmp_path, 'a {% zone "z" %}<p>{{ t }}</p>{% endzone %} b'
        )
        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        response = view(_make_request())
        assert b'<div data-next-zone="z">' in response.content
        assert len(_zone_cache) == 0

    def test_composed_template_feeds_registry_but_get_does_not(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        page_file = _write_zone_page(
            tmp_path, '{% zone "z" %}<p>{{ t }}</p>{% endzone %}'
        )
        module = _load_python_module_memo(page_file)
        view = page_instance._create_unified_view(page_file, {}, module)
        view(_make_request())
        assert len(_zone_cache) == 0

        template = page_instance.composed_template_for(page_file)
        zones = zones_of(template)
        assert set(zones) == {"z"}
        assert len(_zone_cache) == 1


class TestRegistryMemoisationAndInvalidation:
    """`zones_of` keys on the compiled object, recompiles get fresh entries."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _zone_cache.clear()
        yield
        _zone_cache.clear()

    def test_memoised_on_compiled_object(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        page_file = _write_zone_page(
            tmp_path, '{% zone "z" %}<p>{{ t }}</p>{% endzone %}'
        )
        template = page_instance.composed_template_for(page_file)
        assert zones_of(template) is zones_of(template)

    def test_mtime_recompile_adds_distinct_entry(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        djx = tmp_path / "template.djx"
        djx.write_text('{% zone "z" %}<p>{{ t }}</p>{% endzone %}')

        first = page_instance.composed_template_for(page_file)
        first_zones = zones_of(first)

        djx.write_text('{% zone "z" %}<p>EDITED {{ t }}</p>{% endzone %}')
        future = time.time() + 2
        os.utime(djx, (future, future))

        second = page_instance.composed_template_for(page_file)
        assert second is not first
        second_zones = zones_of(second)
        assert second_zones is not first_zones
        # both compiled objects still referenced, so two live entries, no dupes
        assert len(_zone_cache) == 2


class TestRegistryWeakCollection:
    """A stale compiled template is collected out of the registry."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _zone_cache.clear()
        yield
        _zone_cache.clear()

    def test_dropped_template_leaves_the_cache(self) -> None:
        template = Template('{% zone "z" %}<p>{{ t }}</p>{% endzone %}')
        zones_of(template)
        assert len(_zone_cache) == 1
        del template
        gc.collect()
        assert len(_zone_cache) == 0

    def test_recompiled_page_collects_the_previous_object(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        djx = tmp_path / "template.djx"
        djx.write_text('{% zone "z" %}<p>{{ t }}</p>{% endzone %}')

        zones_of(page_instance.composed_template_for(page_file))
        assert len(_zone_cache) == 1

        djx.write_text('{% zone "z" %}<p>v2 {{ t }}</p>{% endzone %}')
        future = time.time() + 2
        os.utime(djx, (future, future))

        # the recompile drops the only strong ref to the old compiled object
        zones_of(page_instance.composed_template_for(page_file))
        gc.collect()
        assert len(_zone_cache) == 1


class TestLayoutAndComponentComposites:
    """Zones in a layout block and beside a component behave correctly."""

    @pytest.fixture(autouse=True)
    def _clear_cache(self):
        _zone_cache.clear()
        yield
        _zone_cache.clear()

    def test_zone_in_layout_block_joins_page_body_zone(
        self, page_instance: Page, tmp_path: Path
    ) -> None:
        (tmp_path / "layout.djx").write_text(
            "<html><body>{% block template %}{% endblock template %}"
            '{% zone "footer" %}<p>{{ f }}</p>{% endzone %}</body></html>'
        )
        page_dir = tmp_path / "sub"
        page_dir.mkdir()
        page_file = page_dir / "page.py"
        page_file.write_text("x = 1")
        (page_dir / "template.djx").write_text(
            '<main>{% zone "main" %}<p>{{ m }}</p>{% endzone %}</main>'
        )

        template = page_instance.composed_template_for(page_file)
        assert set(zones_of(template)) == {"footer", "main"}

        out = page_instance.render(page_file, m="M", f="F")
        assert f'<main><div {ZONE_ATTR}="main"><p>M</p></div></main>' in out
        assert f'<div {ZONE_ATTR}="footer"><p>F</p></div>' in out

    def test_zone_discovered_beside_a_component_tag(self) -> None:
        template = Template(
            '{% component "card" title="Hi" %}'
            '{% zone "side" %}<p>{{ s }}</p>{% endzone %}'
        )
        zones = zones_of(template)
        assert set(zones) == {"side"}
        out = zones["side"].partial.render(Context({"s": "ok"}))
        assert out == "<p>ok</p>"


def _expensive_provider(request: HttpRequest, hits: list[int]) -> str | None:
    """Touch an expensive source only when the request names the zone."""
    if not zone_requested(request, "report"):
        return None
    hits.append(1)
    return "computed"


class TestZoneRequestedGuardsExpensiveProviders:
    """`zone_requested` lets a provider skip expensive work off the zone path."""

    def test_provider_runs_only_when_zone_requested(self) -> None:
        hits: list[int] = []
        requested = RequestFactory().get(
            "/",
            HTTP_X_NEXT_REQUEST="1",
            HTTP_X_NEXT_ZONE="report",
        )
        assert _expensive_provider(requested, hits) == "computed"
        assert hits == [1]

    def test_provider_skips_work_off_the_zone_path(self) -> None:
        hits: list[int] = []
        full = RequestFactory().get("/")
        assert _expensive_provider(full, hits) is None
        assert hits == []

    def test_provider_skips_unrelated_zone_request(self) -> None:
        hits: list[int] = []
        other = RequestFactory().get(
            "/",
            HTTP_X_NEXT_REQUEST="1",
            HTTP_X_NEXT_ZONE="sidebar",
        )
        assert _expensive_provider(other, hits) is None
        assert hits == []
