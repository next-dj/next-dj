"""Public helpers for working with form actions in tests."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from next.discovery import discover_page_registrations
from next.forms.backends import declaring_files
from next.forms.errors import FormActionNotFoundError
from next.forms.manager import form_action_manager
from next.pages.loaders import PAGE_MODULE
from next.utils import resolve_base_dir


if TYPE_CHECKING:
    from os import PathLike

    from django import forms as django_forms


def _page_file(page: str | PathLike[str]) -> str:
    """Return the `page.py` path `page` names, given the file or its directory.

    A relative path reads from `BASE_DIR`, the root the `manage.py` tables print from.
    """
    path = Path(page)
    base_dir = resolve_base_dir()
    if base_dir is not None and not path.is_absolute():
        path = base_dir / path
    if path.is_dir():
        path /= PAGE_MODULE
    return str(path)


def _refuse_ambiguous(action_name: str) -> None:
    """Raise when a bare name lands on one page while other files declare it too."""
    bound = form_action_manager.get_action_meta(action_name)
    if bound is None or bound.get("scope") != "page":
        return
    declared = sorted(set(declaring_files(form_action_manager.backends, action_name)))
    if len(declared) > 1:
        listed = ", ".join(declared)
        msg = (
            f"Action {action_name!r} is declared in several files: {listed}. Pass "
            "page= naming the page.py, or its directory, to pick one."
        )
        raise LookupError(msg)


def resolve_action_url(
    action_name: str, *, page: str | PathLike[str] | None = None
) -> str:
    """Return the reverse URL for a registered form action by name.

    A bare name imports every routed `page.py` first and refuses one several pages
    declare. `page`, the `page.py` or its directory, picks one of them instead.
    """
    if page is not None:
        page_path = _page_file(page)
        try:
            return form_action_manager.get_action_url(action_name, page_path=page_path)
        except FormActionNotFoundError:
            discover_page_registrations()
        return form_action_manager.get_action_url(action_name, page_path=page_path)
    discover_page_registrations()
    url = form_action_manager.get_action_url(action_name)
    _refuse_ambiguous(action_name)
    return url


def build_form_for(
    action_name: str, data: dict[str, Any] | None = None, **form_kwargs
) -> django_forms.Form:
    """Instantiate the form class registered for `action_name`.

    Useful for unit-testing form validation without dispatching an HTTP request. Raises
    `FormActionNotFoundError` with close-match suggestions when the action is unknown
    and `LookupError` when the action is registered without a form class.
    """
    meta = form_action_manager.require_action_meta(action_name)
    form_class = meta.get("form_class")
    if form_class is None:
        msg = (
            f"Action {action_name!r} is registered without a form_class, as "
            "a handler-only action or a wizard, so there is no form to "
            "build. Exercise it by POSTing to resolve_action_url"
            f"({action_name!r}) with the test client instead."
        )
        raise LookupError(msg)
    return cast("django_forms.Form", form_class(data=data, **form_kwargs))


__all__ = ["build_form_for", "resolve_action_url"]
