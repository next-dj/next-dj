"""Where the form checks read their registrations from.

A page-scoped `@action`, form class or `FormWizard` registers only as its `page.py`
runs, so every reader below discovers the page tree first.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from next.checks.common import discover_page_registrations
from next.forms.manager import form_action_manager
from next.forms.registration import RegistrationDiagnostics, registration_diagnostics
from next.pages.loaders import PAGE_MODULE


if TYPE_CHECKING:
    from collections.abc import Iterator

    from next.forms.backends import ActionMeta


def iter_registered_actions() -> "Iterator[ActionMeta]":
    """Yield every action meta from every configured form-action backend."""
    discover_page_registrations()
    for backend in form_action_manager.backends:
        yield from backend.iter_actions()


def action_owners() -> set[Path]:
    """Return the `page.py` paths whose rule an action of their directory answers to."""
    return {
        Path(meta["file_path"]).parent / PAGE_MODULE
        for meta in iter_registered_actions()
    }


def diagnostics() -> RegistrationDiagnostics:
    """Return the registration diagnostics with the page tree discovered first."""
    discover_page_registrations()
    return registration_diagnostics


__all__ = ["action_owners", "diagnostics", "iter_registered_actions"]
