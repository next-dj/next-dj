from collections.abc import Generator
from typing import Any

import pytest
from django.test import override_settings

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded


@pytest.fixture()
def capture_settings_reloaded() -> Generator[list[dict[str, Any]], None, None]:
    events: list[dict[str, Any]] = []

    def _listener(sender, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    settings_reloaded.connect(_listener)
    try:
        yield events
    finally:
        settings_reloaded.disconnect(_listener)


class TestSettingsReloadedSignal:
    """``settings_reloaded`` fires after ``NextFrameworkSettings.reload``."""

    def test_fires_on_explicit_reload(
        self, capture_settings_reloaded: list[dict[str, Any]]
    ) -> None:
        """Calling ``next_framework_settings.reload()`` emits ``settings_reloaded``."""
        next_framework_settings.reload()
        assert len(capture_settings_reloaded) == 1

    def test_sender_is_next_framework_settings_class(
        self, capture_settings_reloaded: list[dict[str, Any]]
    ) -> None:
        """``settings_reloaded`` sender is the ``NextFrameworkSettings`` class."""
        next_framework_settings.reload()
        assert capture_settings_reloaded[0]["sender"] is type(next_framework_settings)

    def test_fires_on_override_settings(
        self, capture_settings_reloaded: list[dict[str, Any]]
    ) -> None:
        """``override_settings(NEXT_FRAMEWORK=...)`` triggers reload via ``setting_changed``."""
        with override_settings(NEXT_FRAMEWORK={}):
            pass
        assert len(capture_settings_reloaded) >= 1

    def test_does_not_fire_for_unrelated_setting(
        self, capture_settings_reloaded: list[dict[str, Any]]
    ) -> None:
        """``override_settings`` for a non-framework key does not emit ``settings_reloaded``."""
        with override_settings(DEBUG=True):
            pass
        assert len(capture_settings_reloaded) == 0
