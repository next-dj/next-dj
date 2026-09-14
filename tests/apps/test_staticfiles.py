from __future__ import annotations

from django.conf import settings
from django.test import override_settings

from next.apps import staticfiles as next_staticfiles
from next.apps.staticfiles import _FINDER_PATH


_APP_DIRS_FINDER = "django.contrib.staticfiles.finders.AppDirectoriesFinder"


class TestInstallIdempotent:
    """A repeated ``install`` leaves a single finder entry."""

    def test_second_call_adds_nothing(self) -> None:
        next_staticfiles.install()
        assert settings.STATICFILES_FINDERS.count(_FINDER_PATH) == 1


class TestFinderFollowsAnOverride:
    """An override of `STATICFILES_FINDERS` drops the finder, so it reinstalls."""

    def test_overridden_list_carries_the_finder(self) -> None:
        with override_settings(STATICFILES_FINDERS=[_APP_DIRS_FINDER]):
            assert settings.STATICFILES_FINDERS == [_APP_DIRS_FINDER, _FINDER_PATH]

    def test_restored_list_still_carries_the_finder(self) -> None:
        with override_settings(STATICFILES_FINDERS=[]):
            pass
        assert _FINDER_PATH in settings.STATICFILES_FINDERS

    def test_another_setting_leaves_the_finders_alone(self) -> None:
        before = settings.STATICFILES_FINDERS
        next_staticfiles._on_setting_changed(setting="INSTALLED_APPS")
        assert settings.STATICFILES_FINDERS is before
