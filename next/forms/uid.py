"""Stable UIDs and related helpers for `@action` endpoints."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings


if TYPE_CHECKING:
    from django.http import HttpRequest


URL_NAME_FORM_ACTION = "form_action"
FORM_ACTION_REVERSE_NAME = "next:form_action"


def _make_uid(action_name: str) -> str:
    """Return a stable short id derived from the action name."""
    raw = f"next.form.action:{action_name}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def validated_next_form_page_path(request: HttpRequest) -> Path | None:  # noqa: PLR0911
    """Return a trusted `page.py` path from POST `_next_form_page`, or `None`.

    Accepts both real `page.py` files and virtual ones — directories whose
    only source is a sibling `template.djx` (the file router already emits
    routes for those; see `FilesystemTreeDispatcher._visit`). The downstream
    renderer (`_load_python_module_memo`, `_load_static_body`) tolerates a
    missing module and falls back to the template, so virtual pages survive
    the re-render path on form-validation failures.
    """
    if not hasattr(request, "POST"):
        return None
    raw = request.POST.get("_next_form_page")
    if not raw or not isinstance(raw, str):
        return None
    raw_stripped = raw.strip()
    if not raw_stripped:
        return None
    try:
        p = Path(raw_stripped).resolve()
    except OSError:
        return None
    if p.name != "page.py":
        return None
    if not p.is_file() and not (p.parent / "template.djx").is_file():
        return None
    base = getattr(settings, "BASE_DIR", None)
    if base is None:
        return None
    try:
        p.relative_to(Path(base).resolve())
    except ValueError:
        return None
    return p


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "URL_NAME_FORM_ACTION",
    "_make_uid",
    "validated_next_form_page_path",
]
