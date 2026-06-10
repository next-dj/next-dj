"""The @action decorator for form-less action handlers."""

import sys
from collections.abc import Callable
from typing import Any

from .backends import ActionRegistration, _resolved_path_str
from .base import _compute_scope
from .manager import form_action_manager
from .registration import registration_diagnostics


def action(
    name: str,
    *,
    form_class: Callable[..., Any] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a form-less callable as a named action.

    Pass `form_class` only as a factory callable. Static Form subclasses
    register automatically through __init_subclass__.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if isinstance(func, type):
            registration_diagnostics.action_applied_to_class.append(func.__qualname__)
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
            ActionRegistration(
                name=name,
                file_path=_resolved_path_str(file_path),
                scope=scope,
                handler=func,
                form_class=form_class,
            )
        )
        return func

    return decorator


__all__ = ["action"]
