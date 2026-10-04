from unittest.mock import patch

from next.ports import page_scripts_slot
from next.scripts.ports import PageScriptsImpl
from next.static import StaticCollector


class TestScriptsImpl:
    """The port hands a render to the scripts manager."""

    def test_the_bound_port_is_the_area_implementation(self) -> None:
        assert isinstance(page_scripts_slot.get(), PageScriptsImpl)

    def test_render_delegates(self) -> None:
        collector = StaticCollector()
        with patch(
            "next.scripts.ports.scripts_manager.render", return_value=("h", {"k": 1})
        ) as render:
            assert PageScriptsImpl().render(
                collector, page_path=None, request=None, nonce="n"
            ) == ("h", {"k": 1})
        render.assert_called_once_with(
            collector, page_path=None, request=None, nonce="n"
        )
