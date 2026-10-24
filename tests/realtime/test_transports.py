import pytest

from next.conf import next_framework_settings
from next.realtime import transports
from next.static import runtime
from next.testing import override_next_settings


@pytest.mark.parametrize(
    ("channels_installed", "requested", "expected"),
    [
        ("no", None, ("sse",)),
        ("yes", None, ("ws", "sse")),
        ("yes", ["sse"], ("sse",)),
        ("no", ["ws"], ("sse",)),
        ("yes", ["ws"], ("ws", "sse")),
        ("yes", ["invalid"], ("sse",)),
    ],
)
def test_active_transports_installation_and_preference_pick_the_order(
    monkeypatch: pytest.MonkeyPatch,
    channels_installed: str,
    requested: list[str] | None,
    expected: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        transports.importlib.util,
        "find_spec",
        lambda _name: object() if channels_installed == "yes" else None,
    )
    monkeypatch.setattr(
        transports.apps, "is_installed", lambda _name: channels_installed == "yes"
    )
    with override_next_settings(
        REALTIME={} if requested is None else {"TRANSPORTS": requested}
    ):
        assert transports.active_transports() == expected


def test_active_transports_repeated_reads_are_cached_until_settings_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probes: list[str] = []

    def find_spec(name: str) -> object:
        probes.append(name)
        return object()

    monkeypatch.setattr(transports.importlib.util, "find_spec", find_spec)
    monkeypatch.setattr(transports.apps, "is_installed", lambda _name: True)
    transports.forget_transports()
    assert transports.active_transports() == ("ws", "sse")
    assert transports.active_transports() == ("ws", "sse")
    assert probes == ["channels"]
    with override_next_settings(REALTIME={"TRANSPORTS": ["sse"]}):
        assert transports.active_transports() == ("sse",)
    assert transports.active_transports() == ("ws", "sse")
    assert len(probes) == 3
    next_framework_settings.reload()
    transports.active_transports()
    assert len(probes) == 4
    transports.forget_transports()


def test_runtime_payload_transports_match_the_shared_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(transports.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(transports.apps, "is_installed", lambda _name: True)
    transports.forget_transports()
    assert runtime._active_transports() == ("ws", "sse")
    transports.forget_transports()
