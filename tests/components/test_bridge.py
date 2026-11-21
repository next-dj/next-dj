import logging
from pathlib import Path

import pytest

from next.components import bridge as bridge_module
from next.components.bridge import bridge
from tests.support.components import component_info


async def _work() -> str:
    return "done"


def _messages(caplog) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == "next.components"
    ]


@pytest.fixture()
def unreported(monkeypatch):
    """Forget every component an earlier test logged a bridge for."""
    monkeypatch.setattr("next.components.bridge._reported", set())


class TestBridgeLog:
    """DEBUG names each component the first time its inline render bridges."""

    def test_debug_logs_once_per_component_with_its_cost(
        self, tmp_path: Path, caplog, settings, unreported
    ) -> None:
        settings.DEBUG = True
        card = component_info(tmp_path / "card", name="card", module=tmp_path / "a.py")
        badge = component_info(
            tmp_path / "badge", name="badge", module=tmp_path / "b.py"
        )
        with caplog.at_level(logging.DEBUG, logger="next.components"):
            for _ in range(2):
                assert bridge(card, _work) == "done"
            assert bridge(badge, _work) == "done"

        messages = _messages(caplog)
        assert len(messages) == 2
        assert messages[0].startswith(
            f"Component 'card' ({tmp_path / 'a.py'}) is rendered through a "
            "sync-to-async bridge on every render, as it has async code and renders "
            "inline in a sync template. The bridge cost "
        )
        assert messages[0].endswith(" ms beyond the component's own work.")
        assert messages[1].startswith("Component 'badge'")

    def test_a_component_another_render_reported_meanwhile_is_not_logged_again(
        self, tmp_path: Path, caplog, settings, unreported
    ) -> None:
        settings.DEBUG = True
        module = tmp_path / "a.py"
        card = component_info(tmp_path / "card", name="card", module=module)

        async def reported_meanwhile() -> str:
            bridge_module._reported.add(module)
            return "done"

        with caplog.at_level(logging.DEBUG, logger="next.components"):
            assert bridge(card, reported_meanwhile) == "done"
        assert _messages(caplog) == []

    def test_without_debug_nothing_is_logged(
        self, tmp_path: Path, caplog, unreported
    ) -> None:
        card = component_info(tmp_path / "card", name="card", module=tmp_path / "a.py")
        with caplog.at_level(logging.DEBUG, logger="next.components"):
            assert bridge(card, _work) == "done"
        assert _messages(caplog) == []
