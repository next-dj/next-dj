import inspect
import pathlib
import pkgutil
from importlib import import_module
from types import ModuleType

from django.core.checks.registry import registry

import next.checks as checks_package
from next.apps import checks as apps_checks
from next.checks.catalog import CHECK_MODULES


_NEXT_ROOT = pathlib.Path(inspect.getfile(checks_package)).parent.parent
_CATALOGUED: tuple[ModuleType, ...] = (*CHECK_MODULES, apps_checks)


def _dotted(directory: pathlib.Path) -> str:
    return ".".join(("next", *directory.relative_to(_NEXT_ROOT).parts))


def _checks_addresses() -> frozenset[str]:
    """Return every checks module or package at any depth, the tag package aside."""
    flat = {f"{_dotted(path.parent)}.checks" for path in _NEXT_ROOT.rglob("checks.py")}
    packages = {_dotted(path.parent) for path in _NEXT_ROOT.rglob("checks/__init__.py")}
    found = (flat | packages) - {checks_package.__name__}
    return frozenset(
        name
        for name in found
        if not any(name.startswith(f"{outer}.") for outer in packages)
    )


def _modules_under(module: ModuleType) -> list[ModuleType]:
    """Return `module` and, for a package, every submodule beneath it."""
    modules = [module]
    if hasattr(module, "__path__"):
        prefix = f"{module.__name__}."
        modules.extend(
            import_module(info.name)
            for info in pkgutil.walk_packages(module.__path__, prefix)
        )
    return modules


def _defined_checks() -> set[object]:
    """Return every `check_*` callable a catalogued module defines itself."""
    return {
        value
        for catalogued in _CATALOGUED
        for module in _modules_under(catalogued)
        for name, value in vars(module).items()
        if name.startswith("check_")
        and callable(value)
        and getattr(value, "__module__", None) == module.__name__
    }


def _registered_checks() -> set[object]:
    return registry.registered_checks | registry.deployment_checks


def _is_catalogued(module_name: str) -> bool:
    return any(
        module_name == module.__name__ or module_name.startswith(f"{module.__name__}.")
        for module in _CATALOGUED
    )


class TestCatalog:
    """`CHECK_MODULES` lists every area's checks, `next.apps` importing its own."""

    def test_every_checks_module_is_catalogued(self) -> None:
        catalogued = {module.__name__ for module in _CATALOGUED}
        assert catalogued == _checks_addresses()

    def test_modules_are_unique(self) -> None:
        assert len(CHECK_MODULES) == len(set(CHECK_MODULES))

    def test_the_apps_checks_stay_out_of_the_catalogue(self) -> None:
        assert apps_checks not in CHECK_MODULES

    def test_every_catalogued_check_is_registered(self) -> None:
        defined = _defined_checks()
        assert defined
        assert defined <= _registered_checks()

    def test_every_registered_framework_check_is_catalogued(self) -> None:
        stray = sorted(
            f"{check.__module__}.{check.__qualname__}"
            for check in _registered_checks()
            if check.__module__.startswith("next.")
            and not _is_catalogued(check.__module__)
        )
        assert stray == []
