"""The @action decorator for named form actions."""

import sys
import types
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, overload

from .backends import (
    ActionGuard,
    ActionRegistration,
    _resolved_path_str,
    build_action_guard,
)
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


@dataclass(frozen=True, slots=True)
class _HandlerSpec:
    """Bundle of @action options threaded into one registration."""

    name: str
    scope: str | None = None
    form_class: "type[Form] | Callable[..., Any] | None" = None
    guard: ActionGuard | None = None


def _register_handler(
    func: Callable[..., Any],
    file_path: str,
    spec: _HandlerSpec,
) -> None:
    """Validate the scope override and forward one registration to the manager."""
    if spec.scope is not None and spec.scope not in _VALID_SCOPES:
        registration_diagnostics.invalid_action_scope.append(
            (func.__qualname__, str(spec.scope))
        )
        return
    form_action_manager.register_action(
        ActionRegistration(
            name=spec.name,
            file_path=_resolved_path_str(file_path),
            scope=spec.scope if spec.scope is not None else _compute_scope(file_path),
            handler=func,
            form_class=spec.form_class,
            guard=spec.guard,
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
    login_required: bool = False,
    permission_required: str | Iterable[str] | None = None,
) -> Callable[[C], C]: ...
def action(
    name: Callable[..., Any] | str | None = None,
    *,
    form_class: "type[Form] | Callable[..., Any] | None" = None,
    scope: str | None = None,
    login_required: bool = False,
    permission_required: str | Iterable[str] | None = None,
) -> Callable[..., Any]:
    """Register a callable as a named form action.

    Used bare or with no name the action is registered under the function's
    own name. `form_class` accepts a factory callable or a Form class that
    does not register its own endpoint. `scope` overrides the file-based
    scope with 'page' or 'shared'. `login_required` and `permission_required`
    guard the endpoint before any POST data is read.
    """
    # The definition file must be captured here. The bare form invokes the
    # inner decorator from this module, so a frame lookup inside it would
    # name decorators.py instead of the defining module.
    file_path = sys._getframe(1).f_code.co_filename
    if isinstance(name, type):
        _record_class_misuse(name)
        return name
    if isinstance(name, types.FunctionType):
        _register_handler(name, file_path, _HandlerSpec(name=name.__name__))
        return name
    if isinstance(form_class, type) and _is_self_registered(form_class):
        msg = (
            f"@action's form_class={form_class.__name__} already registers "
            f"its own endpoint. Set Meta.abstract = True on "
            f"{form_class.__name__}, or move the handler into its on_valid."
        )
        raise TypeError(msg)
    action_name = name if isinstance(name, str) else None
    guard = build_action_guard(
        login_required=login_required,
        permission_required=permission_required,
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if isinstance(func, type):
            _record_class_misuse(func)
            return func
        _register_handler(
            func,
            file_path,
            _HandlerSpec(
                name=action_name if action_name is not None else func.__name__,
                scope=scope,
                form_class=form_class,
                guard=guard,
            ),
        )
        return func

    return decorator


__all__ = ["action"]
