from collections.abc import Generator

import pytest
from django.test import override_settings

from next.conf import next_framework_settings
from next.conf.signals import settings_reloaded
from next.testing import SignalRecorder, capture_signals


@pytest.fixture()
def capture_settings_reloaded() -> Generator[SignalRecorder, None, None]:
    """Record ``settings_reloaded`` emissions."""
    with capture_signals(settings_reloaded) as recorder:
        yield recorder


class TestSettingsReloadedSignal:
    """``settings_reloaded`` fires after ``NextFrameworkSettings.reload``."""

    def test_fires_on_explicit_reload(
        self, capture_settings_reloaded: SignalRecorder
    ) -> None:
        """Calling ``next_framework_settings.reload()`` emits ``settings_reloaded``."""
        next_framework_settings.reload()
        assert len(capture_settings_reloaded) == 1

    def test_sender_is_next_framework_settings_class(
        self, capture_settings_reloaded: SignalRecorder
    ) -> None:
        """``settings_reloaded`` sender is the ``NextFrameworkSettings`` class."""
        next_framework_settings.reload()
        assert capture_settings_reloaded.events[0].sender is type(
            next_framework_settings
        )

    def test_fires_on_override_settings(
        self, capture_settings_reloaded: SignalRecorder
    ) -> None:
        """``override_settings(NEXT_FRAMEWORK=...)`` triggers reload via ``setting_changed``."""
        with override_settings(NEXT_FRAMEWORK={}):
            pass
        assert len(capture_settings_reloaded) >= 1

    def test_does_not_fire_for_unrelated_setting(
        self, capture_settings_reloaded: SignalRecorder
    ) -> None:
        """``override_settings`` for a non-framework key does not emit ``settings_reloaded``."""
        with override_settings(DEBUG=True):
            pass
        assert len(capture_settings_reloaded) == 0
