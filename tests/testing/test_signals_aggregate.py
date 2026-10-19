import importlib
import importlib.util
import pkgutil
from types import ModuleType

import pytest
from django.dispatch import Signal

import next as next_package
from next import signals as aggregate_signals


def _area_signal_modules() -> dict[str, ModuleType]:
    """Import the `signals` module of every framework area that has one."""
    modules: dict[str, ModuleType] = {}
    for info in pkgutil.iter_modules(next_package.__path__):
        if not info.ispkg:
            continue
        name = f"next.{info.name}.signals"
        if importlib.util.find_spec(name) is None:
            continue
        modules[name] = importlib.import_module(name)
    return modules


def _signal_names(module: ModuleType) -> list[str]:
    """Return the names the module binds to a Signal, in declaration order."""
    return [name for name, value in vars(module).items() if isinstance(value, Signal)]


_AREA_MODULES = _area_signal_modules()

_AREA_SIGNALS = [
    pytest.param(name, module_name, id=f"{module_name}.{name}")
    for module_name, module in _AREA_MODULES.items()
    for name in _signal_names(module)
]


class TestAggregateSignalsModule:
    """next.signals re-exports every signal from the subsystems."""

    def test_area_signal_modules_are_discovered(self) -> None:
        assert {"next.forms.signals", "next.partial.signals"} <= set(_AREA_MODULES)

    @pytest.mark.parametrize(("name", "module_name"), _AREA_SIGNALS)
    def test_area_signal_is_reexported(self, name: str, module_name: str) -> None:
        assert name in aggregate_signals.__all__
        owner = _AREA_MODULES[module_name]
        assert getattr(aggregate_signals, name) is getattr(owner, name)

    def test_exports_nothing_beyond_the_area_signals(self) -> None:
        owned = {
            name for module in _AREA_MODULES.values() for name in _signal_names(module)
        }
        assert set(aggregate_signals.__all__) == owned

    def test_every_export_is_a_signal(self) -> None:
        for name in aggregate_signals.__all__:
            assert isinstance(getattr(aggregate_signals, name), Signal)
