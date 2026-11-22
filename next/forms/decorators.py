"""The @action decorator for named form actions."""

import types
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Final, TypedDict, Unpack, overload

from next.introspect import defining_file
from next.transactions import AtomicMode, AtomicOverride

from .backends import (
    ActionGuard,
    ActionRegistration,
    _resolved_path_str,
    build_action_guard,
)
from .base import Form, _compute_scope, _is_self_registered, _meta_atomic
from .manager import form_action_manager
from .registration import checked_atomic, registration_diagnostics
from .transport import Transport, checked_transport


_VALID_SCOPES = ("page", "shared")
_FROM_FORM: Final = object()


class _ActionOptions(TypedDict, total=False):
    """The keyword options `@action` takes besides the action name."""

    form_class: "type[Form] | Callable[..., Any] | None"
    scope: str | None
    login_required: bool
    permission_required: str | Iterable[str] | None
    requires_runtime: bool
    transport: Transport
    atomic: AtomicMode | AtomicOverride | None


_OPTION_NAMES: Final = (
    _ActionOptions.__required_keys__ | _ActionOptions.__optional_keys__
)


def _reject_unknown(options: Mapping[str, object]) -> None:
    """Raise the `TypeError` Python raises for a keyword `@action` does not take."""
    unknown = next((key for key in options if key not in _OPTION_NAMES), None)
    if unknown is not None:
        msg = f"action() got an unexpected keyword argument {unknown!r}"
        raise TypeError(msg)


def _record_class_misuse(cls: type) -> None:
    """Buffer an @action-on-class mistake for the next.E053 system check."""
    # Recording surfaces the mistake in manage.py check, not by aborting django.setup().
    registration_diagnostics.action_applied_to_class.append(cls.__qualname__)


@dataclass(frozen=True, slots=True)
class _HandlerSpec:
    """Bundle of @action options threaded into one registration."""

    name: str
    scope: str | None = None
    form_class: "type[Form] | Callable[..., Any] | None" = None
    guard: ActionGuard | None = None
    requires_runtime: bool = False
    transport: Transport = "http"
    atomic: object = _FROM_FORM


def _atomic_of(spec: _HandlerSpec, owner: str) -> AtomicOverride | None:
    """Return the `atomic=` of `spec`, else the `Meta.atomic` of its form class."""
    if spec.atomic is not _FROM_FORM:
        return checked_atomic(spec.atomic, owner)
    if isinstance(spec.form_class, type):
        return _meta_atomic(spec.form_class)
    return None


def _register_handler(
    func: Callable[..., Any], file_path: str, spec: _HandlerSpec
) -> None:
    """Validate the scope override and forward one registration to the manager."""
    if spec.scope is not None and spec.scope not in _VALID_SCOPES:
        registration_diagnostics.invalid_action_scope.append(
            (func.__qualname__, str(spec.scope))
        )
        return
    owner = f"@action {func.__qualname__!r}"
    transport = checked_transport(spec.transport, owner)
    form_action_manager.register_action(
        ActionRegistration(
            name=spec.name,
            file_path=_resolved_path_str(file_path),
            scope=spec.scope if spec.scope is not None else _compute_scope(file_path),
            handler=func,
            form_class=spec.form_class,
            guard=spec.guard,
            requires_runtime=spec.requires_runtime,
            transport=transport,
            atomic=_atomic_of(spec, owner),
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
    requires_runtime: bool = False,
    transport: Transport = "http",
    atomic: AtomicMode | AtomicOverride | None = None,
) -> Callable[[C], C]: ...
def action(
    name: Callable[..., Any] | str | None = None, **options: Unpack[_ActionOptions]
) -> Callable[..., Any]:
    """Register a callable as a named form action, `transport="any"` admits the socket.

    The guards run after origin resolution and before `get_initial` and form binding.
    """
    _reject_unknown(options)
    if isinstance(name, type):
        _record_class_misuse(name)
        return name
    if isinstance(name, types.FunctionType):
        _register_handler(
            name, str(defining_file(name)), _HandlerSpec(name=name.__name__)
        )
        return name
    form_class = options.get("form_class")
    if isinstance(form_class, type) and _is_self_registered(form_class):
        msg = (
            f"@action's form_class={form_class.__name__} already registers "
            f"its own endpoint. Set Meta.abstract = True on "
            f"{form_class.__name__}, or move the handler into its on_valid."
        )
        raise TypeError(msg)
    action_name = name if isinstance(name, str) else None
    guard = build_action_guard(
        login_required=options.get("login_required", False),
        permission_required=options.get("permission_required"),
    )

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        if isinstance(func, type):
            _record_class_misuse(func)
            return func
        _register_handler(
            func,
            str(defining_file(func)),
            _HandlerSpec(
                name=action_name if action_name is not None else func.__name__,
                scope=options.get("scope"),
                form_class=form_class,
                guard=guard,
                requires_runtime=options.get("requires_runtime", False),
                transport=options.get("transport", "http"),
                atomic=options.get("atomic", _FROM_FORM),
            ),
        )
        return func

    return decorator


__all__ = ["action"]
