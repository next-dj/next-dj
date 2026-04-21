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
    form_class: type[django_forms.Form] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Register a named form action. Names must be unique across the project."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        opts = FormActionOptions(form_class=form_class)
        form_action_manager.register_action(name, func, options=opts)
        return func

    return decorator


__all__ = ["action"]
