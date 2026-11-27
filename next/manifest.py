"""What one area wires at startup, declared in one place the app config walks.

An area names its signal receivers, port bindings, page declarations, lifecycle
participants and checks here, so adding a feature touches no central table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

    from django.dispatch import Signal

    from next.lifecycle import Participant
    from next.pages.declarations import PageDeclaration
    from next.ports import PageScripts, PortSlot


@dataclass(frozen=True, slots=True)
class Bind[T]:
    """One port slot and the implementation an area binds to it."""

    slot: PortSlot[T]
    impl: T

    def apply(self) -> None:
        """Bind the implementation to its slot."""
        self.slot.set(self.impl)


@dataclass(frozen=True, slots=True)
class Manifest:
    """The startup wiring of one area, which `NextFrameworkConfig.ready` applies.

    Receivers connect in manifest order, so that is the order a signal reaches them.
    """

    name: str
    setting_changed: tuple[Callable[..., None], ...] = ()
    settings_reloaded: tuple[Callable[..., None], ...] = ()
    connects: tuple[tuple[Signal, Callable[..., None]], ...] = ()
    ports: tuple[Bind[Any], ...] = ()
    startup: tuple[Callable[[], None], ...] = ()
    declarations: tuple[PageDeclaration[..., Any], ...] = ()
    participants: tuple[Participant, ...] = ()
    providers: tuple[PageScripts, ...] = ()
    checks: ModuleType | None = None
    signals: ModuleType | None = None


__all__ = ["Bind", "Manifest"]
