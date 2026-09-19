from __future__ import annotations

import pytest
from django.conf import settings
from django.test import override_settings

from next.apps import staticfiles as next_staticfiles
from next.apps.staticfiles import (
    _APP_DIRECTORIES_PATH,
    _FINDER_PATH,
    _NEXT_APP_DIRECTORIES_PATH,
)
from tests.support import FINDER_INSTALL_CASES, FinderInstallCase


class TestInstallIdempotent:
    """A repeated ``install`` leaves a single finder entry."""

    def test_second_call_adds_nothing(self) -> None:
        next_staticfiles.install()
        assert settings.STATICFILES_FINDERS.count(_FINDER_PATH) == 1


class TestFinderFollowsAnOverride:
    """An override of `STATICFILES_FINDERS` drops the finder, so it reinstalls."""

    def test_overridden_list_carries_the_finder(self) -> None:
        with override_settings(STATICFILES_FINDERS=[_APP_DIRECTORIES_PATH]):
            assert settings.STATICFILES_FINDERS == [
                _NEXT_APP_DIRECTORIES_PATH,
                _FINDER_PATH,
            ]

    def test_restored_list_still_carries_the_finder(self) -> None:
        with override_settings(STATICFILES_FINDERS=[]):
            pass
        assert _FINDER_PATH in settings.STATICFILES_FINDERS

    def test_another_setting_leaves_the_finders_alone(self) -> None:
        before = settings.STATICFILES_FINDERS
        next_staticfiles._on_setting_changed(setting="INSTALLED_APPS")
        assert settings.STATICFILES_FINDERS is before


class TestFrameworkFindersCollapse:
    """A framework finder named twice ends up installed once, others stay as listed."""

    @pytest.mark.parametrize("case", FINDER_INSTALL_CASES, ids=lambda case: case.id)
    def test_a_configured_list_installs_the_expected_entries(
        self, case: FinderInstallCase
    ) -> None:
        with override_settings(STATICFILES_FINDERS=list(case.configured)):
            assert tuple(settings.STATICFILES_FINDERS) == case.expected

    @pytest.mark.parametrize("case", FINDER_INSTALL_CASES, ids=lambda case: case.id)
    def test_a_second_install_leaves_the_installed_list_alone(
        self, case: FinderInstallCase
    ) -> None:
        with override_settings(STATICFILES_FINDERS=list(case.configured)):
            next_staticfiles.install()
            assert tuple(settings.STATICFILES_FINDERS) == case.expected
