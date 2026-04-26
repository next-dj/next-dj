"""System checks for the forms subsystem."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

from next.conf import import_class_cached


if TYPE_CHECKING:
    from collections.abc import Callable


_FORM_ACTION_BACKEND_SETTINGS_KEY = "DEFAULT_FORM_ACTION_BACKENDS"

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


@register(Tags.compatibility)
def check_form_action_backends_configuration(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate `DEFAULT_FORM_ACTION_BACKENDS` shape and import paths."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    configs = raw.get(_FORM_ACTION_BACKEND_SETTINGS_KEY)
    if configs is None:
        return []
    if not isinstance(configs, list):
        key = _FORM_ACTION_BACKEND_SETTINGS_KEY
        return [
            Error(
                f"NEXT_FRAMEWORK[{key!r}] must be a list.",
                obj=settings,
                id="next.E044",
            ),
        ]
    errors: list[CheckMessage] = []
    for index, config in enumerate(configs):
        prefix = f"NEXT_FRAMEWORK['{_FORM_ACTION_BACKEND_SETTINGS_KEY}'][{index}]"
        errors.extend(_validate_single_form_action_backend(config, prefix))
    return errors


def _validate_single_form_action_backend(
    config: object,
    prefix: str,
) -> list[CheckMessage]:
    if not isinstance(config, dict):
        return [Error(f"{prefix} must be a dict.", obj=settings, id="next.E044")]
    backend_path = config.get("BACKEND")
    if not isinstance(backend_path, str):
        return [
            Error(
                f"{prefix}.BACKEND must be a string.",
                obj=settings,
                id="next.E044",
            ),
        ]
    from .backends import FormActionBackend  # noqa: PLC0415

    try:
        cls = import_class_cached(backend_path)
    except ImportError as exc:
        return [
            Error(
                f"{prefix}.BACKEND {backend_path!r} cannot be imported: {exc}.",
                obj=settings,
                id="next.E044",
            ),
        ]
    if not isinstance(cls, type) or not issubclass(cls, FormActionBackend):
        return [
            Error(
                f"{prefix}.BACKEND {backend_path!r} must subclass FormActionBackend.",
                obj=settings,
                id="next.E045",
            ),
        ]
    return []


__all__ = [
    "check_form_action_backends_configuration",
    "check_form_action_collisions",
    "clear_action_collisions",
    "record_possible_collision",
]
