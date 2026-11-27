from unittest.mock import patch

from next.ports import ScriptShare, page_scripts
from next.scripts.ports import PageScriptsImpl
from next.static.runtime import CONSENT_PAYLOAD_KEY, SCRIPTS_PAYLOAD_KEY
from tests.support.ports import StubRender


class TestScriptsProvider:
    """The provider hands a render to the scripts manager."""

    def test_the_registered_provider_is_the_area_one(self) -> None:
        found = [item for item in page_scripts if item.name == "scripts"]
        assert [type(item) for item in found] == [PageScriptsImpl]
        assert found[0].applies() is True
        assert found[0].keys == {SCRIPTS_PAYLOAD_KEY, CONSENT_PAYLOAD_KEY}

    def test_the_share_carries_the_head_and_the_entries(self) -> None:
        render = StubRender()
        with patch(
            "next.scripts.ports.scripts_manager.render", return_value=("h", {"k": 1})
        ) as rendered:
            share = PageScriptsImpl().share(render)
        assert share == ScriptShare(head="h", entries={"k": 1})
        rendered.assert_called_once_with(
            render.collector, page_path=None, request=None, nonce=render.nonce
        )

    def test_a_render_without_scripts_shares_nothing(self) -> None:
        with patch("next.scripts.ports.scripts_manager.render", return_value=("", {})):
            assert PageScriptsImpl().share(StubRender()) is None
