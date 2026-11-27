import copy
import warnings
from dataclasses import dataclass
from types import FunctionType

from django.conf import settings
from django.core.signals import setting_changed
from django.dispatch import Signal

from next import ports
from next.caches import sources
from next.conf import USER_SETTING
from next.conf.signals import settings_reloaded
from next.lifecycle import lifecycle
from next.urls.signals import routes_reloaded


_MISSING = object()

# The signals whose framework receivers move the sources or act on a reload.
_WIRED: tuple[tuple[str, Signal], ...] = (
    ("setting_changed", setting_changed),
    ("settings_reloaded", settings_reloaded),
    ("routes_reloaded", routes_reloaded),
)


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The process state a test may change only through a context it closes."""

    values: dict[str, object]
    framework: object
    slots: dict[str, object]
    participants: tuple[object, ...]
    sources: tuple[str, ...]
    receivers: frozenset[tuple[str, str]]


def snapshot() -> Snapshot:
    """Record the settings, each port, the participants, the sources and their wiring.

    The copy catches a test that edits the framework mapping in place.
    """
    with warnings.catch_warnings():
        # Reading some settings warns, and the snapshot reads every one of them.
        warnings.simplefilter("ignore")
        values = {
            name: getattr(settings, name) for name in dir(settings) if name.isupper()
        }
    framework = copy.deepcopy(values.get(USER_SETTING, _MISSING))
    slots = {
        name: _binding(slot.peek())
        for name, slot in vars(ports).items()
        if isinstance(slot, ports.PortSlot)
    }
    slots.update(
        (name, tuple((item.name, type(item)) for item in listed))
        for name, listed in vars(ports).items()
        if isinstance(listed, ports.PortList)
    )
    participants = tuple((item.name, type(item)) for item in lifecycle)
    named = tuple(source.name for source in sources())
    return Snapshot(values, framework, slots, participants, named, _receivers())


def _receivers() -> frozenset[tuple[str, str]]:
    """Return each framework receiver connected to a signal that drops the memos.

    A test that disconnects one leaves every later memo held past a settings change.
    """
    return frozenset(
        (name, key[0])
        for name, signal in _WIRED
        for key, *_ in signal.receivers
        if isinstance(key[0], str) and key[0].startswith("next.")
    )


def _binding(impl: object) -> object:
    """Return a function itself and the class of any other port implementation.

    A second `ready()` binds fresh instances of the same stateless classes.
    """
    return impl if isinstance(impl, FunctionType) else type(impl)


def leaked(before: Snapshot, after: Snapshot) -> list[str]:
    """Name each setting, port, participant, source and receiver `after` holds apart."""
    names = sorted(before.values.keys() | after.values.keys())
    found = [
        f"settings.{name}"
        for name in names
        if before.values.get(name, _MISSING) != after.values.get(name, _MISSING)
    ]
    if before.framework != after.values.get(USER_SETTING, _MISSING):
        found.append(f"the contents of settings.{USER_SETTING}")
    found.extend(
        f"next.ports.{name}"
        for name in sorted(before.slots)
        if before.slots[name] != after.slots.get(name)
    )
    if before.participants != after.participants:
        found.append("next.lifecycle.lifecycle")
    if before.sources != after.sources:
        found.append("next.caches.sources()")
    found.extend(
        f"the {receiver} receiver of {signal}"
        for signal, receiver in sorted(before.receivers - after.receivers)
    )
    return found
