"""Public helpers for working with form actions in tests."""

from __future__ import annotations

import difflib
from typing import TYPE_CHECKING, Any, cast

from next.forms.backends import FormActionNotFound
from next.forms.manager import form_action_manager


if TYPE_CHECKING:
    from django import forms as django_forms


def resolve_action_url(action_name: str) -> str:
    """Return the reverse URL for a registered form action by name.

    Wraps `form_action_manager.get_action_url` so tests do not need to
    import the manager singleton directly. Raises `FormActionNotFound`
    for unknown actions.
    """
    return form_action_manager.get_action_url(action_name)


def build_form_for(
    action_name: str,
    data: dict[str, Any] | None = None,
    **form_kwargs: Any,  # noqa: ANN401
) -> django_forms.Form:
    """Instantiate the form class registered for `action_name`.

    Useful for unit-testing form validation without dispatching an HTTP
    request. Raises `FormActionNotFound` with close-match suggestions when
    the action is unknown and `LookupError` when the action is registered
    without a form class.
    """
    meta = form_action_manager.get_action_meta(action_name)
    if meta is None:
        known = {
            registered_name
            for backend in form_action_manager.backends
            for registered in backend.iter_actions()
            if (registered_name := registered.get("name")) is not None
        }
        suggestions = tuple(difflib.get_close_matches(action_name, sorted(known)))
        raise FormActionNotFound(
            name=action_name,
            suggestions=suggestions,
            registry_empty=not known,
        )
    form_class = meta.get("form_class")
    if form_class is None:
        msg = f"Action {action_name!r} has no form_class"
        raise LookupError(msg)
    return cast("django_forms.Form", form_class(data=data, **form_kwargs))


__all__ = ["build_form_for", "resolve_action_url"]
