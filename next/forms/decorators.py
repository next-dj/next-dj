"""The @action decorator for form-less action handlers."""

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .base import _compute_scope
from .manager import form_action_manager


_action_applied_to_class: list[str] = []


def clear_action_applied_to_class() -> None:
    """Drop the recorded @action-on-class misuses. For test isolation."""
    _action_applied_to_class.clear()


def action(
    name: str,
    *,
    form_class: Callable[..., Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a callable as a named action.

    For form-less functions, omit `form_class`. For actions that need a
    dynamically constructed form class (e.g. a DI factory), pass it as
    `form_class`. Do NOT pass a static Form subclass — those register
    automatically via __init_subclass__.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if isinstance(func, type):
            _action_applied_to_class.append(func.__qualname__)
            msg = (
                "@action is for form-less actions only. "
                "Form classes register automatically through __init_subclass__."
            )
            raise TypeError(msg)
        if isinstance(form_class, type):
            msg = (
                "@action's form_class must be a factory callable, not a Form class. "
                "Form classes register automatically through __init_subclass__."
            )
            raise TypeError(msg)
        frame = sys._getframe(1)
        file_path = frame.f_code.co_filename
        scope = _compute_scope(file_path)
        form_action_manager.register_action(
            name,
            handler=func,
            form_class=form_class,
            file_path=str(Path(file_path).resolve()),
            scope=scope,
        )
        return func

    return decorator


__all__ = ["action", "clear_action_applied_to_class"]
