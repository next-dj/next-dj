import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.test import override_settings
from django.utils import module_loading

from next.forms import autodiscover
from next.forms.autodiscover import autodiscover_forms
from next.forms.manager import form_action_manager
from next.forms.signals import action_registered
from tests.support import AppFactory, throwaway_apps


@pytest.fixture()
def make_app(tmp_path: Path) -> Iterator[AppFactory]:
    """Write throwaway app packages that stand for every installed app."""
    with throwaway_apps(tmp_path, keep_installed=False) as make:
        yield make


def test_imports_valid_forms_and_registers_shared(settings, tmp_path, make_app) -> None:
    settings.BASE_DIR = tmp_path
    body = (
        "from next.forms import Form, CharField\n\n\n"
        "class DiscoveredSharedForm(Form):\n"
        "    name = CharField()\n"
    )
    make_app(forms=body)

    autodiscover_forms()

    assert (
        form_action_manager.default_backend.get_meta("discovered_shared_form")
        is not None
    )


def test_missing_forms_module_is_skipped(make_app) -> None:
    app = make_app()

    autodiscover_forms()

    assert f"{app}.forms" not in sys.modules


def test_broken_forms_module_propagates(make_app) -> None:
    make_app(forms="import a_module_that_does_not_exist\n")

    with pytest.raises(ModuleNotFoundError):
        autodiscover_forms()


def test_imported_forms_module_lands_in_sys_modules(make_app) -> None:
    app = make_app(forms="value = 1\n")

    autodiscover_forms()

    assert f"{app}.forms" in sys.modules


def test_second_call_does_not_reregister(settings, tmp_path, make_app) -> None:
    settings.BASE_DIR = tmp_path
    body = (
        "from next.forms import Form, CharField\n\n\n"
        "class RediscoveredForm(Form):\n"
        "    name = CharField()\n"
    )
    make_app(forms=body)
    autodiscover_forms()

    events: list[object] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append(kwargs.get("action_name"))

    action_registered.connect(_listener)
    try:
        autodiscover_forms()
    finally:
        action_registered.disconnect(_listener)
    assert events == []


def test_disabled_setting_skips_import(make_app, monkeypatch) -> None:
    make_app(forms="value = 1\n")

    def _boom(_target: str) -> None:
        pytest.fail("import attempted while disabled")

    monkeypatch.setattr(module_loading, "import_module", _boom)
    with override_settings(NEXT_FRAMEWORK={"FORM_AUTODISCOVER": False}):
        autodiscover_forms()


def test_disabled_setting_skips_discovery_entirely(monkeypatch) -> None:
    def _boom(*args: str) -> None:
        pytest.fail("discovery attempted while disabled")

    monkeypatch.setattr(autodiscover, "autodiscover_modules", _boom)
    with override_settings(NEXT_FRAMEWORK={"FORM_AUTODISCOVER": False}):
        autodiscover_forms()


def test_processes_multiple_apps(settings, tmp_path, make_app) -> None:
    settings.BASE_DIR = tmp_path
    make_app(
        forms=(
            "from next.forms import Form, CharField\n\n\n"
            "class FirstAppForm(Form):\n"
            "    name = CharField()\n"
        )
    )
    second = make_app()
    make_app(
        forms=(
            "from next.forms import Form, CharField\n\n\n"
            "class ThirdAppForm(Form):\n"
            "    name = CharField()\n"
        )
    )

    autodiscover_forms()

    backend = form_action_manager.default_backend
    assert backend.get_meta("first_app_form") is not None
    assert backend.get_meta("third_app_form") is not None
    assert f"{second}.forms" not in sys.modules


@pytest.mark.usefixtures("make_app")
def test_empty_app_list_is_noop(monkeypatch) -> None:
    def _boom(_target: str) -> None:
        pytest.fail("import attempted with no installed apps")

    monkeypatch.setattr(module_loading, "import_module", _boom)
    autodiscover_forms()
