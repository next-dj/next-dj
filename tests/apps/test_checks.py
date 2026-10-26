from __future__ import annotations

from unittest.mock import patch

from django.core.checks import Warning as DjangoWarning
from django.test import override_settings

from next.apps import checks
from next.apps.checks import (
    check_builtin_tag_libraries_complete,
    check_django_templates_backend_present,
)
from next.apps.templates import _BUILTIN_MODULES
from next.server import asgi
from next.testing import override_next_settings
from tests.support import check_ids


_DJANGO_BACKEND = "django.template.backends.django.DjangoTemplates"
_JINJA_BACKEND = "django.template.backends.jinja2.Jinja2"


class TestDjangoTemplatesBackendCheck:
    """``check_django_templates_backend_present`` guards tag availability."""

    def test_missing_backend_emits_w062(self) -> None:
        with override_settings(TEMPLATES=[{"BACKEND": _JINJA_BACKEND}]):
            messages = check_django_templates_backend_present(app_configs=None)
        assert check_ids(messages) == ["next.W062"]
        assert isinstance(messages[0], DjangoWarning)

    def test_empty_templates_emits_w062(self) -> None:
        with override_settings(TEMPLATES=[]):
            messages = check_django_templates_backend_present(app_configs=None)
        assert check_ids(messages) == ["next.W062"]

    def test_present_backend_emits_nothing(self) -> None:
        with override_settings(TEMPLATES=[{"BACKEND": _DJANGO_BACKEND}]):
            messages = check_django_templates_backend_present(app_configs=None)
        assert messages == []

    def test_mixed_engines_with_django_backend_pass(self) -> None:
        with override_settings(
            TEMPLATES=[{"BACKEND": _JINJA_BACKEND}, {"BACKEND": _DJANGO_BACKEND}]
        ):
            messages = check_django_templates_backend_present(app_configs=None)
        assert messages == []


class TestBuiltinTagLibrariesComplete:
    """``check_builtin_tag_libraries_complete`` guards the builtin tuple."""

    def test_discovery_finds_every_builtin(self) -> None:
        discovered = checks._iter_tag_library_modules()
        assert set(discovered) == set(_BUILTIN_MODULES)

    def test_complete_list_emits_nothing(self) -> None:
        messages = check_builtin_tag_libraries_complete(app_configs=None)
        assert messages == []

    def test_unregistered_library_emits_w063(self) -> None:
        with patch.object(checks, "_BUILTIN_MODULES", ("next.templatetags.forms",)):
            messages = check_builtin_tag_libraries_complete(app_configs=None)
        assert check_ids(messages) == ["next.W063"] * (len(_BUILTIN_MODULES) - 1)
        assert all(isinstance(m, DjangoWarning) for m in messages)
        objs = {m.obj for m in messages}
        assert "next.templatetags.forms" not in objs
        assert "next.templatetags.partial" in objs


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
