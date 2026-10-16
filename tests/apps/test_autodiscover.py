import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.test import override_settings

from next.apps import autodiscover
from tests.support import AppFactory, throwaway_apps


_RECORDING_BODY = "from . import LOADS\n\nLOADS.append(__name__)\n"
_DEPS_PROBE_BODY = (
    "import sys\n\n"
    "SEEN = sorted(\n"
    "    name for name in sys.modules\n"
    "    if name.startswith('discovered_') and name.endswith('.deps')\n"
    ")\n"
)
_PERMISSION_BODY = (
    "from next.permissions import add_perm\n\n"
    "add_perm(__name__.partition('.')[0] + '.probe', lambda user, obj: True)\n"
)
_PERMISSIONS_PROBE_BODY = (
    "from next.permissions.manager import permission_manager\n\n"
    "SEEN = tuple(name for name, _rule in permission_manager.registry.items())\n"
)
_SWITCHES = (
    ("deps", "DEPS_AUTODISCOVER"),
    ("permissions", "PERMISSIONS_AUTODISCOVER"),
    ("forms", "FORM_AUTODISCOVER"),
    ("signals", "SIGNALS_AUTODISCOVER"),
)
_MODULE_NAMES = tuple(module for module, _key in _SWITCHES)


@pytest.fixture()
def make_app(tmp_path: Path) -> Iterator[AppFactory]:
    """Write throwaway app packages and append them to the installed apps."""
    with throwaway_apps(tmp_path) as make:
        yield make


def _full_app(make_app: AppFactory) -> str:
    return make_app(**dict.fromkeys(_MODULE_NAMES, _RECORDING_BODY))


def _imported(*apps: str) -> list[str]:
    """Return the app submodules in the order they entered `sys.modules`."""
    return [
        name
        for name in sys.modules
        if name.partition(".")[0] in apps and name.rpartition(".")[2] in _MODULE_NAMES
    ]


def _loads(app: str) -> list[str]:
    return sys.modules[app].LOADS


class TestImportOrder:
    """Each module name finishes across every app before the next name starts."""

    def test_deps_then_permissions_then_forms_then_signals(self, make_app) -> None:
        first = _full_app(make_app)
        second = _full_app(make_app)
        autodiscover.install()
        assert _imported(first, second) == [
            f"{first}.deps",
            f"{second}.deps",
            f"{first}.permissions",
            f"{second}.permissions",
            f"{first}.forms",
            f"{second}.forms",
            f"{first}.signals",
            f"{second}.signals",
        ]

    def test_a_form_module_sees_the_deps_of_a_later_app(self, make_app) -> None:
        earlier = make_app(forms=_DEPS_PROBE_BODY)
        later = make_app(deps=_RECORDING_BODY)
        autodiscover.install()
        assert f"{later}.deps" in sys.modules[f"{earlier}.forms"].SEEN

    @pytest.mark.usefixtures("isolated_permissions")
    def test_a_form_module_sees_the_permissions_of_a_later_app(self, make_app) -> None:
        earlier = make_app(forms=_PERMISSIONS_PROBE_BODY)
        later = make_app(permissions=_PERMISSION_BODY)
        autodiscover.install()
        assert (f"{later}.probe",) == sys.modules[f"{earlier}.forms"].SEEN


class TestSwitches:
    """Every module name has its own bool setting, each one silencing only itself."""

    @pytest.mark.parametrize(("module", "key"), _SWITCHES, ids=_MODULE_NAMES)
    def test_a_disabled_key_skips_its_module_only(
        self, make_app, module: str, key: str
    ) -> None:
        app = _full_app(make_app)
        with override_settings(NEXT_FRAMEWORK={key: False}):
            autodiscover.install()
        assert _loads(app) == [
            f"{app}.{name}" for name in _MODULE_NAMES if name != module
        ]

    def test_every_key_off_imports_nothing(self, make_app) -> None:
        app = _full_app(make_app)
        switches = {key: False for _module, key in _SWITCHES}
        with override_settings(NEXT_FRAMEWORK=switches):
            autodiscover.install()
        assert _loads(app) == []


class TestModuleFailures:
    """A missing module is skipped and a broken one surfaces its own error."""

    @pytest.mark.parametrize("module", _MODULE_NAMES)
    def test_a_missing_module_is_skipped_quietly(self, make_app, module: str) -> None:
        bodies = {name: _RECORDING_BODY for name in _MODULE_NAMES if name != module}
        app = make_app(**bodies)
        autodiscover.install()
        assert f"{app}.{module}" not in sys.modules
        assert _loads(app) == [f"{app}.{name}" for name in bodies]

    @pytest.mark.parametrize("module", _MODULE_NAMES)
    def test_a_broken_module_raises_its_own_error(self, make_app, module: str) -> None:
        make_app(**{module: f"raise LookupError('{module} is broken')\n"})
        with pytest.raises(LookupError, match=f"^{module} is broken$"):
            autodiscover.install()

    @pytest.mark.parametrize("module", _MODULE_NAMES)
    def test_a_missing_import_inside_a_module_propagates(
        self, make_app, module: str
    ) -> None:
        make_app(**{module: "import a_module_that_does_not_exist\n"})
        with pytest.raises(ModuleNotFoundError, match="a_module_that_does_not_exist"):
            autodiscover.install()


class TestRepeatedDiscovery:
    """A second pass finds every module already imported and runs no body again."""

    def test_a_second_install_imports_nothing_twice(self, make_app) -> None:
        app = _full_app(make_app)
        autodiscover.install()
        autodiscover.install()
        assert _loads(app) == [f"{app}.{name}" for name in _MODULE_NAMES]

    def test_a_second_ready_imports_nothing_twice(self, make_app) -> None:
        app = _full_app(make_app)
        config = django_apps.get_app_config("next")
        config.ready()
        config.ready()
        assert _loads(app) == [f"{app}.{name}" for name in _MODULE_NAMES]
