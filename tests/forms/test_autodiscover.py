import importlib
import types
import uuid

import pytest
from django.test import override_settings

from next.forms import autodiscover
from next.forms.autodiscover import autodiscover_forms, clear_discovered
from next.forms.manager import form_action_manager


@pytest.fixture(autouse=True)
def _reset_discovered():
    clear_discovered()
    yield
    clear_discovered()


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
    monkeypatch.setattr(autodiscover.apps, "get_app_configs", lambda: list(configs))


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

    assert f"{app.name}.forms" not in autodiscover._discovered


def test_broken_forms_module_propagates(tmp_path, monkeypatch) -> None:
    app = _make_package(
        tmp_path, monkeypatch, forms_body="import a_module_that_does_not_exist\n"
    )
    _patch_app_configs(monkeypatch, [app])

    with pytest.raises(ModuleNotFoundError):
        autodiscover_forms()


def test_records_discovered_target(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])

    autodiscover_forms()

    assert f"{app.name}.forms" in autodiscover._discovered


def test_second_call_does_not_reimport(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])
    autodiscover_forms()

    def _boom(_target):
        pytest.fail("re-import attempted")

    monkeypatch.setattr(autodiscover.importlib, "import_module", _boom)
    autodiscover_forms()


def test_clear_discovered_allows_reimport(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])
    autodiscover_forms()
    assert f"{app.name}.forms" in autodiscover._discovered

    clear_discovered()

    assert f"{app.name}.forms" not in autodiscover._discovered
    calls: list[str] = []
    real_import = importlib.import_module
    monkeypatch.setattr(
        autodiscover.importlib,
        "import_module",
        lambda target: calls.append(target) or real_import(target),
    )
    autodiscover_forms()
    assert f"{app.name}.forms" in calls


def test_disabled_setting_short_circuits(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])

    with override_settings(NEXT_FRAMEWORK={"FORM_AUTODISCOVER": False}):
        autodiscover_forms()

    assert autodiscover._discovered == set()


def test_disabled_setting_skips_import(tmp_path, monkeypatch) -> None:
    app = _make_package(tmp_path, monkeypatch, forms_body="value = 1\n")
    _patch_app_configs(monkeypatch, [app])

    def _boom(_target):
        pytest.fail("import attempted while disabled")

    monkeypatch.setattr(autodiscover.importlib, "import_module", _boom)
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
    assert f"{first.name}.forms" in autodiscover._discovered
    assert f"{third.name}.forms" in autodiscover._discovered
    assert f"{second.name}.forms" not in autodiscover._discovered


def test_empty_app_list_is_noop(monkeypatch) -> None:
    _patch_app_configs(monkeypatch, [])

    autodiscover_forms()

    assert autodiscover._discovered == set()
