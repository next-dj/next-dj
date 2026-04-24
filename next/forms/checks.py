"""System checks for the forms subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register


if TYPE_CHECKING:
    from collections.abc import Callable


_action_collisions: dict[str, set[tuple[str, str]]] = {}


def _handler_fingerprint(handler: Callable[..., Any]) -> tuple[str, str]:
    """Return a module+qualname tuple that survives reloads for one function."""
    module = getattr(handler, "__module__", "") or ""
    qualname = getattr(handler, "__qualname__", "") or getattr(handler, "__name__", "")
    return (str(module), str(qualname))


def record_possible_collision(
    action_name: str,
    old_handler: Callable[..., Any],
    new_handler: Callable[..., Any],
) -> None:
    """Record a collision when a name is re-registered with a distinct handler.

    Called by `RegistryFormActionBackend.register_action` only on the
    overwrite path, so the common first-registration case pays nothing.
    Identity match (module reload of the exact same object) short-circuits
    before the fingerprint comparison.
    """
    if old_handler is new_handler:
        return
    old_fp = _handler_fingerprint(old_handler)
    new_fp = _handler_fingerprint(new_handler)
    if old_fp == new_fp:
        return
    _action_collisions.setdefault(action_name, {old_fp}).add(new_fp)


def clear_action_collisions() -> None:
    """Drop the collision-check state. Intended for test isolation."""
    _action_collisions.clear()


@register(Tags.compatibility)
def check_form_action_collisions(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Flag two `@action` calls that share a name but come from different handlers.

    Re-registration of the same handler (for example during autoreload) is
    safe and does not trigger the check because the fingerprint stays
    identical.
    """
    return [
        Error(
            f"Form action {name!r} is registered by {len(fps)} different "
            "handlers. Rename one of them or change the namespace to avoid "
            "the collision.",
            obj=settings,
            id="next.E041",
        )
        for name, fps in _action_collisions.items()
    ]


__all__ = [
    "check_form_action_collisions",
    "clear_action_collisions",
    "record_possible_collision",
]
