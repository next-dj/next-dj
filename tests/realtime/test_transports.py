from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from next.conf import next_framework_settings
from next.realtime import transports
from next.static import runtime
from next.testing import override_next_settings, override_websocket_mount


@pytest.fixture(autouse=True)
def unmounted() -> Iterator[None]:
    with override_websocket_mount():
        yield


@pytest.mark.parametrize(
    ("mounted", "requested", "expected"),
    [
        ("no", None, ("sse",)),
        ("yes", None, ("ws", "sse")),
        ("yes", ["sse"], ("sse",)),
        ("no", ["ws"], ("ws", "sse")),
        ("yes", ["ws"], ("ws", "sse")),
        ("yes", ["invalid"], ("sse",)),
    ],
)
def test_active_transports_mount_and_preference_pick_the_order(
    mounted: str, requested: list[str] | None, expected: tuple[str, ...]
) -> None:
    if mounted == "yes":
        transports.announce_websocket()
    with override_next_settings(
        REALTIME={} if requested is None else {"TRANSPORTS": requested}
    ):
        assert transports.active_transports() == expected


def test_announce_websocket_enables_ws_for_the_cached_default() -> None:
    assert transports.active_transports() == ("sse",)
    transports.announce_websocket()
    assert transports.active_transports() == ("ws", "sse")


def test_active_transports_repeated_reads_are_cached_until_settings_change() -> None:
    transports.announce_websocket()
    first = transports.active_transports()
    assert transports.active_transports() is first
    with override_next_settings(REALTIME={"TRANSPORTS": ["sse"]}):
        assert transports.active_transports() == ("sse",)
    assert transports.active_transports() == ("ws", "sse")
    next_framework_settings.reload()
    assert transports.active_transports() == ("ws", "sse")


def test_runtime_payload_transports_match_the_shared_resolution() -> None:
    transports.announce_websocket()
    transports.forget_transports()
    assert runtime._active_transports() == ("ws", "sse")


@pytest.mark.parametrize("requested", [("ws",), ("sse", "ws"), ["ws", "invalid"]])
def test_active_transports_any_ws_entry_enables_it_without_a_mount(
    requested: object,
) -> None:
    with override_next_settings(REALTIME={"TRANSPORTS": requested}):
        assert transports.active_transports() == ("ws", "sse")
    assert transports.withdraw_websocket() is False


@pytest.mark.parametrize("mounted", ["no", "yes"])
def test_active_transports_non_mapping_setting_follows_the_mount(
    monkeypatch: pytest.MonkeyPatch, mounted: str
) -> None:
    if mounted == "yes":
        transports.announce_websocket()
    monkeypatch.setattr(
        transports,
        "next_framework_settings",
        Mock(spec=transports.next_framework_settings, REALTIME="invalid"),
    )
    transports.forget_transports()
    assert transports.active_transports() == (
        ("ws", "sse") if mounted == "yes" else ("sse",)
    )


def test_announce_websocket_drops_the_memoised_page_offer() -> None:
    runtime.forget_realtime_offer()
    before = runtime.realtime_offer()
    assert before.websocket is False
    assert runtime.realtime_offer() is before
    transports.announce_websocket()
    after = runtime.realtime_offer()
    assert after.websocket is True
    assert '"transports":["ws","sse"]' in after.entry


def test_forget_transports_drops_the_memoised_page_offer() -> None:
    transports.announce_websocket()
    transports.forget_transports()
    assert runtime.realtime_offer().websocket is True
    transports._websocket_mounted.clear()
    assert runtime.realtime_offer().websocket is True
    transports.forget_transports()
    assert runtime.realtime_offer() == runtime.RealtimeOffer("", websocket=False)


def test_withdraw_websocket_reports_and_forgets_the_announcement() -> None:
    transports.announce_websocket()
    assert transports.active_transports() == ("ws", "sse")
    assert transports.withdraw_websocket() is True
    assert transports.active_transports() == ("sse",)
    assert transports.withdraw_websocket() is False


@pytest.mark.parametrize("before", ["no", "yes"])
@pytest.mark.parametrize("inside", ["no", "yes"])
def test_override_websocket_mount_sets_the_flag_and_restores_it(
    before: str, inside: str
) -> None:
    if before == "yes":
        transports.announce_websocket()
    with override_websocket_mount(mounted=inside == "yes"):
        assert ("ws" in transports.active_transports()) is (inside == "yes")
        transports.announce_websocket()
    assert ("ws" in transports.active_transports()) is (before == "yes")
