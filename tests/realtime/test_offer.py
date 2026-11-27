import json
from collections.abc import Iterator

import pytest

from next.partial.manager import partial_backend_manager
from next.ports import ScriptShare, page_scripts
from next.realtime import transports
from next.realtime.offer import DEFAULT_TRANSPORTS, RealtimeScripts
from next.static.runtime import REALTIME_PAYLOAD_KEY, WS_CHUNK_KEY
from next.testing import override_next_settings, override_websocket_mount
from tests.support.codecs import TextStreamCodec
from tests.support.ports import StubRender


WEBSOCKET = {"TRANSPORTS": ["ws", "sse"]}
NEXT_PATCHES = "application/vnd.next.patches+json"


@pytest.fixture(autouse=True)
def unmounted() -> Iterator[None]:
    with override_websocket_mount():
        yield


def _offer(share: ScriptShare | None) -> dict[str, object]:
    assert share is not None
    key, _, value = share.shared.partition(":")
    assert json.loads(key) == REALTIME_PAYLOAD_KEY
    return json.loads(value)


class TestRealtimeScripts:
    """The `$realtime` entry is shared only where it moves off the runtime defaults."""

    def test_the_registered_provider_is_the_area_one(self) -> None:
        found = [item for item in page_scripts if item.name == "realtime"]
        assert [type(item) for item in found] == [RealtimeScripts]
        assert found[0].applies() is True
        assert found[0].keys == {REALTIME_PAYLOAD_KEY}

    def test_the_stock_configuration_shares_nothing(self) -> None:
        assert transports.active_transports() == DEFAULT_TRANSPORTS
        assert RealtimeScripts().share(StubRender()) is None

    def test_an_active_websocket_offers_both_transports_and_lists_its_chunk(
        self,
    ) -> None:
        with override_next_settings(REALTIME=WEBSOCKET):
            share = RealtimeScripts().share(StubRender())
        assert share is not None
        assert share.listed == {WS_CHUNK_KEY}
        assert _offer(share) == {
            "protocol": 2,
            "transports": ["ws", "sse"],
            "content_type": NEXT_PATCHES,
        }

    def test_a_mounted_websocket_offers_it_without_a_reload(self) -> None:
        assert RealtimeScripts().share(StubRender()) is None
        transports.announce_websocket()
        share = RealtimeScripts().share(StubRender())
        assert share is not None
        assert share.listed == {WS_CHUNK_KEY}

    def test_another_partial_backend_names_its_content_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            partial_backend_manager,
            "_select_config",
            lambda: {"BACKEND": "tests.support.codecs.TextStreamCodec"},
        )
        partial_backend_manager.reset()
        try:
            assert isinstance(partial_backend_manager.get(), TextStreamCodec)
            share = RealtimeScripts().share(StubRender())
        finally:
            monkeypatch.undo()
            partial_backend_manager.reset()
        assert share is not None
        assert share.listed == frozenset()
        assert _offer(share)["content_type"] == "text/plain"
        assert RealtimeScripts().share(StubRender()) is None

    def test_one_configuration_shares_one_object(self) -> None:
        with override_next_settings(REALTIME=WEBSOCKET):
            first = RealtimeScripts().share(StubRender())
            assert RealtimeScripts().share(StubRender()) is first
