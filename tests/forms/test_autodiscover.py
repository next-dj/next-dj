import importlib
import sys
import types
import uuid

import pytest
from django.apps import apps as django_apps
from django.test import override_settings
from django.utils import module_loading

from next.forms import autodiscover
from next.forms.autodiscover import autodiscover_forms
from next.forms.manager import form_action_manager
from next.forms.signals import action_registered


def _make_package(
    tmp_path, monkeypatch, *, forms_body: str | None
) -> types.SimpleNamespace:
    pkg_name = f"fakeapp_{uuid.uuid4().hex}"
    pkg_dir = tmp_path / pkg_name
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    if forms_body is not None:
        (pkg_dir / "forms.py").write_text(forms_body)
    monkeypatch.syspath_prepend(str(tmp_path))
    module = importlib.import_module(pkg_name)
    return types.SimpleNamespace(name=pkg_name, module=module)


def _patch_app_configs(monkeypatch, configs) -> None:
    monkeypatch.setattr(django_apps, "get_app_configs", lambda: list(configs))


def test_imports_valid_forms_and_registers_shared(
    settings, tmp_path, monkeypatch
) -> None:
    settings.BASE_DIR = tmp_path
    body = (
        "from next.forms import Form, CharField\n\n\n"
        "class DiscoveredSharedForm(Form):\n"
        "    name = CharField()\n"
    )
    app = _make_package(tmp_path, monkeypatch, forms_body=body)
    _patch_app_configs(monkeypatch, [app])

    autodiscover_forms()

    assert (
        form_action_manager.default_backend.get_meta("discovered_shared_form")
        is not None
    )


def test_missing_forms_module_is_skipped(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body=None)
    _patch_app_configs(monkeypatch, [app])

    autodiscover_forms()

    assert f"{app.name}.forms" not in sys.modules


def test_broken_forms_module_propagates(tmp_path, monkeypatch) -> None:
    app = _make_package(
        tmp_path, monkeypatch, forms_body="import a_module_that_does_not_exist\n"
    )
    _patch_app_configs(monkeypatch, [app])

    with pytest.raises(ModuleNotFoundError):
        autodiscover_forms()


def test_imported_forms_module_lands_in_sys_modules(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])

    autodiscover_forms()

    assert f"{app.name}.forms" in sys.modules


def test_second_call_does_not_reregister(settings, tmp_path, monkeypatch) -> None:
    settings.BASE_DIR = tmp_path
    body = (
        "from next.forms import Form, CharField\n\n\n"
        "class RediscoveredForm(Form):\n"
        "    name = CharField()\n"
    )
    app = _make_package(tmp_path, monkeypatch, forms_body=body)
    _patch_app_configs(monkeypatch, [app])
    autodiscover_forms()

    events: list[object] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append(kwargs.get("action_name"))

    action_registered.connect(_listener)
    try:
        autodiscover_forms()
    finally:
        action_registered.disconnect(_listener)
    assert events == []


def test_disabled_setting_skips_import(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])

    def _boom(_target: str) -> None:
        pytest.fail("import attempted while disabled")

    monkeypatch.setattr(module_loading, "import_module", _boom)
    with override_settings(NEXT_FRAMEWORK={"FORM_AUTODISCOVER": False}):
        autodiscover_forms()


def test_disabled_setting_skips_discovery_entirely(monkeypatch) -> None:
    def _boom(*_args: str) -> None:
        pytest.fail("discovery attempted while disabled")

    monkeypatch.setattr(autodiscover, "autodiscover_modules", _boom)
    with override_settings(NEXT_FRAMEWORK={"FORM_AUTODISCOVER": False}):
        autodiscover_forms()


def test_processes_multiple_apps(settings, tmp_path, monkeypatch) -> None:
    settings.BASE_DIR = tmp_path
    first = _make_package(
        tmp_path,
        monkeypatch,
        forms_body=(
            "from next.forms import Form, CharField\n\n\n"
            "class FirstAppForm(Form):\n"
            "    name = CharField()\n"
        ),
    )
    second = _make_package(tmp_path, monkeypatch, forms_body=None)
    third = _make_package(
        tmp_path,
        monkeypatch,
        forms_body=(
            "from next.forms import Form, CharField\n\n\n"
            "class ThirdAppForm(Form):\n"
            "    name = CharField()\n"
        ),
    )
    _patch_app_configs(monkeypatch, [first, second, third])

    autodiscover_forms()

    backend = form_action_manager.default_backend
    assert backend.get_meta("first_app_form") is not None
    assert backend.get_meta("third_app_form") is not None
    assert f"{second.name}.forms" not in sys.modules


def test_empty_app_list_is_noop(monkeypatch) -> None:
    _patch_app_configs(monkeypatch, [])

    def _boom(_target: str) -> None:
        pytest.fail("import attempted with no installed apps")

    monkeypatch.setattr(module_loading, "import_module", _boom)
    autodiscover_forms()
