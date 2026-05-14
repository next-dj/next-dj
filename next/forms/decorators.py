"""The `@action` decorator used to register form handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .backends import FormActionOptions
from .manager import form_action_manager


if TYPE_CHECKING:
    from collections.abc import Callable

    from django import forms as django_forms


def action(
    name: str,
    *,
    form_class: type[django_forms.Form]
    | Callable[..., type[django_forms.Form]]
    | None = None,
    namespace: str | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a named form action. Names must be unique across the project.

    Pass `namespace="app_label"` to prefix the stored key with
    `"app_label:"`, which lets two apps use the same short name without
    colliding. Reverse is by the namespaced name.

    `form_class` may be a `Form` subclass or a callable that returns one
    when called. Factory callables are dependency-resolved at dispatch
    time with the request and URL kwargs, which lets admin-style
    handlers shape the form per request (for example via
    `ModelAdmin.get_form()`).
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        opts = FormActionOptions(form_class=form_class, namespace=namespace)
        full_name = f"{namespace}:{name}" if namespace else name
        form_action_manager.register_action(full_name, func, options=opts)
        return func

    return decorator


__all__ = ["action"]
