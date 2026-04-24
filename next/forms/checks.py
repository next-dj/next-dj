"""System checks for the forms subsystem."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

from .signals import action_registered


if TYPE_CHECKING:
    from collections.abc import Callable


_action_fingerprints: dict[str, set[tuple[str, str]]] = defaultdict(set)


def _handler_fingerprint(handler: Callable[..., Any]) -> tuple[str, str]:
    """Return a module+qualname tuple that survives reloads for one function."""
    module = getattr(handler, "__module__", "") or ""
    qualname = getattr(handler, "__qualname__", "") or getattr(handler, "__name__", "")
    return (str(module), str(qualname))


def _track_action_registration(
    sender: object,  # noqa: ARG001
    **kwargs: object,
) -> None:
    """Record a unique handler fingerprint each time an action is registered."""
    action_name = kwargs.get("action_name")
    handler = kwargs.get("handler")
    if not isinstance(action_name, str) or not callable(handler):
        return
    _action_fingerprints[action_name].add(_handler_fingerprint(handler))


action_registered.connect(_track_action_registration)


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
        for name, fps in _action_fingerprints.items()
        if len(fps) > 1
    ]


__all__ = ["check_form_action_collisions"]
