from collections.abc import Iterator

import pytest
from django.test import override_settings

from next.conf import next_framework_settings
from next.realtime import transports
from next.testing import override_next_settings, override_websocket_mount


@pytest.fixture(autouse=True)
def unmounted() -> Iterator[None]:
    with override_websocket_mount():
        yield


@pytest.fixture()
def unwarned() -> Iterator[None]:
    transports._unmounted_warned.clear()
    yield
    transports._unmounted_warned.clear()


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


@pytest.mark.parametrize("requested", [("ws",), ("sse", "ws"), ["ws", "invalid"]])
def test_active_transports_any_ws_entry_enables_it_without_a_mount(
    requested: object,
) -> None:
    with override_next_settings(REALTIME={"TRANSPORTS": requested}):
        assert transports.active_transports() == ("ws", "sse")
    assert transports.withdraw_websocket() is False


def test_active_transports_read_the_default_of_a_non_mapping_setting() -> None:
    expected = transports.active_transports()
    with override_settings(NEXT_FRAMEWORK={"REALTIME": "invalid"}):
        assert transports.active_transports() == expected


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


@pytest.mark.usefixtures("unwarned")
def test_an_offered_ws_without_a_mounted_socket_warns_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", "next.realtime.transports"):
        transports.warn_unmounted_websocket()
        transports.warn_unmounted_websocket()
    (record,) = caplog.records
    assert record.name == "next.realtime.transports"
    assert "lists 'ws' but this process mounts no WebSocket" in record.getMessage()


@pytest.mark.usefixtures("unwarned")
def test_an_offered_ws_with_a_mounted_socket_stays_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transports.announce_websocket()
    with caplog.at_level("WARNING", "next.realtime.transports"):
        transports.warn_unmounted_websocket()
    assert caplog.records == []
    assert not transports._unmounted_warned.is_set()


@pytest.mark.usefixtures("unwarned")
def test_a_second_thread_that_lost_the_race_stays_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with transports._warning_lock:
        transports._unmounted_warned.set()
    with caplog.at_level("WARNING", "next.realtime.transports"):
        transports.warn_unmounted_websocket()
    assert caplog.records == []
