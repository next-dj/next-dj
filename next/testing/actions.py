"""Public helpers for working with form actions in tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from next.forms.manager import form_action_manager


if TYPE_CHECKING:
    from django import forms as django_forms


def resolve_action_url(action_name: str) -> str:
    """Return the reverse URL for a registered form action by name.

    Wraps `form_action_manager.get_action_url` so tests do not need to
    import the manager singleton directly. Raises `KeyError` for
    unknown actions.
    """
    return form_action_manager.get_action_url(action_name)


def build_form_for(
    action_name: str,
    data: dict[str, Any] | None = None,
    **form_kwargs: Any,  # noqa: ANN401
) -> django_forms.Form:
    """Instantiate the form class registered for `action_name`.

    Useful for unit-testing form validation without dispatching an HTTP
    request. Raises `KeyError` when the action is unknown and
    `LookupError` when the action is registered without a form class.
    """
    meta = form_action_manager.default_backend.get_meta(action_name)
    if meta is None:
        msg = f"Unknown form action: {action_name}"
        raise KeyError(msg)
    form_class = meta.get("form_class")
    if form_class is None:
        msg = f"Action {action_name!r} has no form_class"
        raise LookupError(msg)
    return cast("django_forms.Form", form_class(data=data, **form_kwargs))


__all__ = ["build_form_for", "resolve_action_url"]
