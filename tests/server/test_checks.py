from unittest.mock import patch

import pytest
from django.test import override_settings

from next.server import asgi, checks
from next.server.checks import check_dev_server
from next.testing import override_next_settings
from tests.support import check_ids


class RecordingServer:
    def run(self, app, **options: object) -> None:
        """Serve nothing."""


def test_the_default_server_reports_nothing() -> None:
    assert check_dev_server() == []


def test_a_server_class_reports_nothing() -> None:
    with override_next_settings(DEV_SERVER=f"{__name__}.RecordingServer"):
        assert check_dev_server() == []


@pytest.mark.parametrize("path", [f"{__name__}.Missing", "builtins.object"])
def test_a_path_naming_no_server_reports_e212(path: str) -> None:
    with override_next_settings(DEV_SERVER=path):
        assert check_ids(check_dev_server()) == ["next.E212"]


class TestRunserverOverrideOrderCheck:
    """``check_runserver_override_order`` guards the uvicorn ``runserver``."""

    @staticmethod
    def _ids(apps_list: list[str], *, uvicorn: bool = True) -> list[str]:
        spec = object() if uvicorn else None
        with (
            override_settings(INSTALLED_APPS=apps_list),
            patch.object(asgi.importlib.util, "find_spec", return_value=spec),
        ):
            return check_ids(checks.check_runserver_override_order(app_configs=None))

    def test_staticfiles_above_next_emits_w148(self) -> None:
        assert self._ids(["django.contrib.staticfiles", "next"]) == ["next.W148"]

    def test_next_above_staticfiles_is_quiet(self) -> None:
        assert self._ids(["next", "django.contrib.staticfiles"]) == []

    def test_without_staticfiles_is_quiet(self) -> None:
        assert self._ids(["next"]) == []

    def test_a_named_server_warns_without_uvicorn(self) -> None:
        apps_list = ["django.contrib.staticfiles", "next"]
        with override_next_settings(DEV_SERVER="config.devserver.Server"):
            assert self._ids(apps_list, uvicorn=False) == ["next.W148"]

    def test_without_uvicorn_is_quiet(self) -> None:
        apps_list = ["django.contrib.staticfiles", "next"]
        assert self._ids(apps_list, uvicorn=False) == []

    def test_any_app_above_next_with_a_runserver_emits_w148(self) -> None:
        with (
            patch.object(checks, "get_commands", return_value={"runserver": "daphne"}),
            patch.object(asgi.importlib.util, "find_spec", return_value=object()),
        ):
            messages = checks.check_runserver_override_order(app_configs=None)
        assert check_ids(messages) == ["next.W148"]
        assert "daphne" in messages[0].msg
