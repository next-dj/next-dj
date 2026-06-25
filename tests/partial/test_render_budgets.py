from unittest.mock import patch

import next.partial.patches as patches_module
from next.testing import NextClient, envelope_of


class TestOneZoneRenderPerInvalidPost:
    """A partial invalid POST renders the failed form's zone exactly once.

    A failing assertion here means a change started rendering the zone
    body more than once for one POST, paying twice for the same HTML.
    """

    def test_zone_body_renders_once(self, next_client: NextClient) -> None:
        original = patches_module.Patches._render_zone
        with patch.object(
            patches_module.Patches,
            "_render_zone",
            autospec=True,
            side_effect=original,
        ) as spy:
            response = next_client.post_action(
                "zoned_rename_form",
                {"title": ""},
                origin="/board_settings/",
                partial=True,
                zones="rename-board",
            )
        assert envelope_of(response).zone_targets() == ["rename-board"]
        assert spy.call_count == 1
