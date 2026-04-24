"""System checks for the forms subsystem."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register


if TYPE_CHECKING:
    from collections.abc import Callable


_action_fingerprints: dict[str, set[tuple[str, str]]] = defaultdict(set)


def _handler_fingerprint(handler: Callable[..., Any]) -> tuple[str, str]:
    """Return a module+qualname tuple that survives reloads for one function."""
    module = getattr(handler, "__module__", "") or ""
    qualname = getattr(handler, "__qualname__", "") or getattr(handler, "__name__", "")
    return (str(module), str(qualname))


def track_action_registration(
    action_name: str,
    handler: Callable[..., Any],
) -> None:
    """Record a unique handler fingerprint for ``action_name``.

    Called directly by ``RegistryFormActionBackend.register_action`` so the
    check has data to inspect without paying Django-signal-dispatch cost
    on every registration. Public ``action_registered`` receivers are still
    notified via the signal in the backend.
    """
    _action_fingerprints[action_name].add(_handler_fingerprint(handler))


def clear_action_fingerprints() -> None:
    """Drop the collision-check state. Intended for test isolation."""
    _action_fingerprints.clear()


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


__all__ = [
    "check_form_action_collisions",
    "clear_action_fingerprints",
    "track_action_registration",
]
