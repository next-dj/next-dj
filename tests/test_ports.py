import inspect

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpResponse
from django.template import Template
from django.test import override_settings

from next.components.ports import ComponentTagsImpl
from next.pages.ports import PageScanImpl
from next.partial.ports import PartialShaperImpl
from next.ports import (
    ComponentTags,
    PageScan,
    PartialShaper,
    PortSlot,
    RouterAccess,
    SeoRoutes,
    StaticAssets,
    component_tags_slot,
    page_scan_slot,
    partial_shaper_slot,
    router_access_slot,
    seo_routes_slot,
    static_assets_slot,
)
from next.seo.ports import SeoRoutesImpl
from next.static.manager import StaticManager
from next.static.ports import StaticAssetsImpl
from next.urls import FileRouterBackend, RouterManager, URLPatternParser
from next.urls.ports import RouterAccessImpl
from tests.support import IntentOnlyShaper, component_info, file_router_config_entry


def _methods_of(port: type) -> list[str]:
    return sorted(
        name
        for name, member in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


def _port_methods() -> list[str]:
    return _methods_of(PartialShaper)


def _call_shape(owner: type, name: str) -> list[tuple[str, object]]:
    """Parameter names and kinds, with the defaults an implementation may add."""
    signature = inspect.signature(getattr(owner, name))
    return [(param.name, param.kind) for param in signature.parameters.values()]


def _parameter_shape(owner: type, name: str) -> list[tuple[str, object, object]]:
    signature = inspect.signature(getattr(owner, name))
    return [
        (param.name, param.kind, param.default)
        for param in signature.parameters.values()
    ]


PROCESS_SLOTS = [
    pytest.param(component_tags_slot, ComponentTagsImpl, id="component_tags"),
    pytest.param(page_scan_slot, PageScanImpl, id="page_scan"),
    pytest.param(partial_shaper_slot, PartialShaperImpl, id="partial_shaper"),
    pytest.param(router_access_slot, RouterAccessImpl, id="router_access"),
    pytest.param(seo_routes_slot, SeoRoutesImpl, id="seo_routes"),
    pytest.param(static_assets_slot, StaticAssetsImpl, id="static_assets"),
]

SLOT_SUBJECTS = [
    "component tags port",
    "page scan port",
    "partial shaper",
    "router access port",
    "seo routes port",
    "static assets port",
]


class TestUnboundSlot:
    """An unbound slot fails loudly instead of answering None."""

    def test_get_raises_before_set(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="unbound"):
            PortSlot("partial shaper").get()

    @pytest.mark.parametrize("subject", SLOT_SUBJECTS)
    def test_the_message_names_the_subject_the_slot_was_built_with(
        self, subject
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=subject):
            PortSlot(subject).get()

    def test_the_message_names_the_hook_that_binds_the_slot(self) -> None:
        """A read this early means the app never started, so the fix is named."""
        with pytest.raises(ImproperlyConfigured) as caught:
            PortSlot("page scan port").get()

        assert "NextFrameworkConfig.ready()" in str(caught.value)
        assert "never finished starting" in str(caught.value)

    def test_peek_answers_none_before_set(self) -> None:
        """An early reader can tell the app is not ready without catching an error."""
        assert PortSlot("seo routes port").peek() is None

    def test_a_slot_carries_no_instance_dictionary(self) -> None:
        """Four process-wide singletons, so the slot stays a two-field object."""
        assert not hasattr(PortSlot("partial shaper"), "__dict__")


class TestBoundSlot:
    """A bound slot answers the very object it was given."""

    def test_get_returns_the_bound_object(self) -> None:
        slot = PortSlot("partial shaper")
        shaper = IntentOnlyShaper()
        slot.set(shaper)
        assert slot.get() is shaper
        assert slot.peek() is shaper

    def test_set_replaces_the_previous_binding(self) -> None:
        slot = PortSlot("partial shaper")
        slot.set(IntentOnlyShaper())
        replacement = IntentOnlyShaper()
        slot.set(replacement)
        assert slot.get() is replacement


class TestPartialShaperPort:
    """Both implementations keep the exact call shape the port declares.

    mypy checks the real implementation but never reads `tests/`, so the
    stub the intent-gate tests bind is compared against the port here.
    """

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _port_methods() == [
            "intent",
            "set_vary",
            "shape_response",
            "shape_validate",
            "zone_response",
        ]

    @pytest.mark.parametrize("name", _port_methods())
    @pytest.mark.parametrize(
        "implementation", [PartialShaperImpl, IntentOnlyShaper], ids=["real", "stub"]
    )
    def test_implementation_parameters_match_the_port(
        self, implementation, name
    ) -> None:
        assert _parameter_shape(implementation, name) == _parameter_shape(
            PartialShaper, name
        )

    def test_set_vary_declares_the_partial_request_headers(self) -> None:
        """A shared cache serves one shape where the other belongs without this."""
        response = HttpResponse("<p>ok</p>")

        PartialShaperImpl().set_vary(response)

        assert "X-Next-Request" in response.headers["Vary"]
        assert "X-Next-Zone" in response.headers["Vary"]


class TestPageScanPort:
    """The scan port reaches the pages area without discovery importing it."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(PageScan) == ["load_scanned_page_modules"]

    def test_implementation_parameters_match_the_port(self) -> None:
        assert _call_shape(PageScanImpl, "load_scanned_page_modules") == _call_shape(
            PageScan, "load_scanned_page_modules"
        )

    def test_the_scan_answers_the_routed_pages_of_the_given_manager(
        self, tmp_path
    ) -> None:
        pages = tmp_path / "blog"
        pages.mkdir()
        page_file = pages / "page.py"
        page_file.write_text('template = "ok"\n')
        entry = file_router_config_entry(pages_dir=tmp_path)

        with override_settings(NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry]}):
            manager = RouterAccessImpl().create_manager()
            loaded = PageScanImpl().load_scanned_page_modules(manager)

        assert loaded == [("blog", page_file)]


class TestRouterAccessPort:
    """The router port builds exactly what the urls area would build itself."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(RouterAccess) == [
            "create_backend",
            "create_manager",
            "url_parser",
        ]

    @pytest.mark.parametrize("name", ["create_backend", "create_manager", "url_parser"])
    def test_implementation_parameters_match_the_port(self, name) -> None:
        assert _call_shape(RouterAccessImpl, name) == _call_shape(RouterAccess, name)

    def test_create_backend_builds_the_router_the_entry_names(self, tmp_path) -> None:
        backend = RouterAccessImpl().create_backend(
            file_router_config_entry(pages_dir=tmp_path)
        )
        assert isinstance(backend, FileRouterBackend)

    def test_create_manager_builds_a_fresh_manager(self) -> None:
        access = RouterAccessImpl()
        first = access.create_manager()
        assert isinstance(first, RouterManager)
        assert access.create_manager() is not first

    def test_url_parser_is_the_shared_one_the_file_router_routes_through(self) -> None:
        """One parser, so a caller off the port reads the same memoised patterns."""
        access = RouterAccessImpl()
        parser = access.url_parser()

        assert isinstance(parser, URLPatternParser)
        assert access.url_parser() is parser


class TestStaticAssetsPort:
    """The static port matches the manager the render path used to import."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(StaticAssets) == [
            "collect_component_assets",
            "create_collector",
            "discover_page_assets",
            "inject",
        ]

    @pytest.mark.parametrize(
        "name", ["create_collector", "discover_page_assets", "inject"]
    )
    def test_the_static_manager_matches_the_port(self, name) -> None:
        assert _call_shape(StaticManager, name) == _call_shape(StaticAssets, name)

    @pytest.mark.parametrize("name", _methods_of(StaticAssets))
    def test_implementation_parameters_match_the_port(self, name) -> None:
        assert _call_shape(StaticAssetsImpl, name) == _call_shape(StaticAssets, name)

    def test_create_collector_answers_a_fresh_sink_per_render(self) -> None:
        assets = StaticAssetsImpl()
        assert assets.create_collector() is not assets.create_collector()

    def test_collect_component_assets_gathers_the_co_located_files(
        self, tmp_path
    ) -> None:
        """A composite component carries its own CSS, which the render has to reach."""
        folder = tmp_path / "_components" / "card"
        folder.mkdir(parents=True)
        (folder / "component.css").write_text(".card {}\n")
        info = component_info(folder, name="card", template="<p>c</p>\n")
        assets = StaticAssetsImpl()
        collector = assets.create_collector()

        assets.collect_component_assets(info, collector)

        assert [asset.kind for asset in collector.assets_in_slot("styles")] == ["css"]

    def test_collect_component_assets_tolerates_no_collector(self, tmp_path) -> None:
        """A component rendered outside a page render has nowhere to collect into."""
        folder = tmp_path / "_components" / "card"
        folder.mkdir(parents=True)
        (folder / "component.css").write_text(".card {}\n")
        info = component_info(folder, name="card", template="<p>c</p>\n")

        assert StaticAssetsImpl().collect_component_assets(info, None) is None


class TestSeoRoutesPort:
    """The SEO port hands the lazy urlpatterns its routes without an import."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(SeoRoutes) == ["patterns"]

    def test_implementation_parameters_match_the_port(self) -> None:
        assert _call_shape(SeoRoutesImpl, "patterns") == _call_shape(
            SeoRoutes, "patterns"
        )


class TestComponentTagsPort:
    """The tags port answers from the node the component tag library compiles to."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert _methods_of(ComponentTags) == ["component_names"]

    def test_implementation_parameters_match_the_port(self) -> None:
        assert _call_shape(ComponentTagsImpl, "component_names") == _call_shape(
            ComponentTags, "component_names"
        )

    def test_component_names_include_a_nested_component(self) -> None:
        source = '{% #component "card" %}{% component "badge" %}{% /component %}'
        names = ComponentTagsImpl().component_names(Template(source).nodelist)
        assert sorted(names) == ["badge", "card"]

    def test_a_template_without_components_names_none(self) -> None:
        assert ComponentTagsImpl().component_names(Template("<p>x</p>").nodelist) == []


class TestSubscriptedSlotSingletons:
    """A subscripted slot is the plain holder, so the four singletons behave alike."""

    @pytest.mark.parametrize(("slot", "_impl"), PROCESS_SLOTS)
    def test_the_process_slots_are_port_slots(self, slot, _impl) -> None:
        assert isinstance(slot, PortSlot)


class TestAppComposition:
    """`AppConfig.ready` leaves the process-wide slots bound to the real objects."""

    @pytest.mark.parametrize(("slot", "impl"), PROCESS_SLOTS)
    def test_the_process_slot_holds_its_implementation(self, slot, impl) -> None:
        assert isinstance(slot.get(), impl)
