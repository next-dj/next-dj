"""The @action decorator for named form actions."""

import sys
import types
from collections.abc import Callable
from typing import Any, overload

from .backends import ActionRegistration, _resolved_path_str
from .base import Form, _compute_scope, _is_self_registered
from .manager import form_action_manager
from .registration import registration_diagnostics


_VALID_SCOPES = ("page", "shared")


def _record_class_misuse(cls: type) -> None:
    """Buffer an @action-on-class mistake for the next.E053 system check."""
    # Recording instead of raising lets the mistake surface through
    # manage.py check rather than a bare TypeError aborting django.setup()
    # mid-import.
    registration_diagnostics.action_applied_to_class.append(cls.__qualname__)


def _register_handler(
    func: Callable[..., Any],
    *,
    name: str,
    file_path: str,
    scope: str | None,
    form_class: "type[Form] | Callable[..., Any] | None",
) -> None:
    """Validate the scope override and forward one registration to the manager."""
    if scope is not None and scope not in _VALID_SCOPES:
        registration_diagnostics.invalid_action_scope.append(
            (func.__qualname__, str(scope))
        )
        return
    form_action_manager.register_action(
        ActionRegistration(
            name=name,
            file_path=_resolved_path_str(file_path),
            scope=scope if scope is not None else _compute_scope(file_path),
            handler=func,
            form_class=form_class,
        )
    )


@overload
def action[C: Callable[..., Any]](name: C, /) -> C: ...
@overload
def action[C: Callable[..., Any]](
    name: str | None = None,
    *,
    form_class: "type[Form] | Callable[..., Any] | None" = None,
    scope: str | None = None,
) -> Callable[[C], C]: ...
def action(
    name: Callable[..., Any] | str | None = None,
    *,
    form_class: "type[Form] | Callable[..., Any] | None" = None,
    scope: str | None = None,
) -> Callable[..., Any]:
    """Register a callable as a named form action.

    Used bare or with no name the action is registered under the function's
    own name. `form_class` accepts a factory callable or a Form class that
    does not register its own endpoint. `scope` overrides the file-based
    scope with 'page' or 'shared'.
    """
    # The definition file must be captured here. The bare form invokes the
    # inner decorator from this module, so a frame lookup inside it would
    # name decorators.py instead of the defining module.
    file_path = sys._getframe(1).f_code.co_filename
    if isinstance(name, type):
        _record_class_misuse(name)
        return name
    if isinstance(name, types.FunctionType):
        _register_handler(
            name,
            name=name.__name__,
            file_path=file_path,
            scope=None,
            form_class=None,
        )
        return name
    if isinstance(form_class, type) and _is_self_registered(form_class):
        msg = (
            f"@action's form_class={form_class.__name__} already registers "
            f"its own endpoint. Set Meta.abstract = True on "
            f"{form_class.__name__}, or move the handler into its on_valid."
        )
        raise TypeError(msg)
    action_name = name if isinstance(name, str) else None

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if isinstance(func, type):
            _record_class_misuse(func)
            return func
        _register_handler(
            func,
            name=action_name if action_name is not None else func.__name__,
            file_path=file_path,
            scope=scope,
            form_class=form_class,
        )
        return func

    return decorator


__all__ = ["action"]
