import logging
from collections.abc import Generator

import pytest
from django.core.exceptions import ImproperlyConfigured
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

    def test_every_receiver_runs_before_an_error_leaves_the_reload(self) -> None:
        """A receiver that raises does not stop the ones connected behind it."""
        ran: list[str] = []
        message = "bad value"

        def failing(**kwargs) -> None:
            ran.append("failing")
            raise ImproperlyConfigured(message)

        def following(**kwargs) -> None:
            ran.append("following")

        settings_reloaded.connect(failing)
        settings_reloaded.connect(following)
        try:
            with pytest.raises(ImproperlyConfigured, match="bad value"):
                next_framework_settings.reload()
        finally:
            settings_reloaded.disconnect(failing)
            settings_reloaded.disconnect(following)
        assert ran == ["failing", "following"]

    def test_a_failure_behind_the_first_one_is_logged(self, caplog) -> None:
        """The second failing receiver leaves a record instead of vanishing."""
        first_message = "first bad value"
        second_message = "second bad value"

        def failing(**kwargs) -> None:
            raise ImproperlyConfigured(first_message)

        def also_failing(**kwargs) -> None:
            raise ImproperlyConfigured(second_message)

        settings_reloaded.connect(failing)
        settings_reloaded.connect(also_failing)
        try:
            with (
                caplog.at_level(logging.ERROR, logger="next.conf.signals"),
                pytest.raises(ImproperlyConfigured, match=first_message),
            ):
                next_framework_settings.reload()
        finally:
            settings_reloaded.disconnect(failing)
            settings_reloaded.disconnect(also_failing)
        assert "also_failing" in caplog.text
        assert second_message in caplog.text

    def test_does_not_fire_for_unrelated_setting(
        self, capture_settings_reloaded: SignalRecorder
    ) -> None:
        """``override_settings`` for a non-framework key does not emit ``settings_reloaded``."""
        with override_settings(DEBUG=True):
            pass
        assert len(capture_settings_reloaded) == 0
