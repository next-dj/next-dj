import pytest

from next.ports import StaticAssets
from next.static.manager import StaticManager
from next.static.ports import StaticAssetsImpl
from tests.support import call_shape, component_info, port_methods


class TestStaticAssetsPort:
    """The static port matches the call shapes of `StaticManager`."""

    def test_the_port_declares_the_expected_methods(self) -> None:
        assert port_methods(StaticAssets) == [
            "collect_component_assets",
            "create_collector",
            "discover_page_assets",
            "inject",
        ]

    @pytest.mark.parametrize(
        "name", ["create_collector", "discover_page_assets", "inject"]
    )
    def test_the_static_manager_matches_the_port(self, name) -> None:
        assert call_shape(StaticManager, name) == call_shape(StaticAssets, name)

    @pytest.mark.parametrize("name", port_methods(StaticAssets))
    def test_implementation_parameters_match_the_port(self, name) -> None:
        assert call_shape(StaticAssetsImpl, name) == call_shape(StaticAssets, name)

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
