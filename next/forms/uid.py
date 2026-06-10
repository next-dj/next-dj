"""UID generation and redirect helpers for form actions."""

from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch


URL_NAME_FORM_ACTION = "form_action"
FORM_ACTION_REVERSE_NAME = "next:form_action"


def reverse_form_action(uid: str) -> str:
    """Return the dispatch URL for a form action uid.

    The route name depends on how the project wired next URLs: included
    under the `next` namespace it reverses as `next:form_action`, included
    bare it reverses as `form_action`.
    """
    try:
        return reverse(FORM_ACTION_REVERSE_NAME, kwargs={"uid": uid})
    except NoReverseMatch:
        return reverse(URL_NAME_FORM_ACTION, kwargs={"uid": uid})


def _posted_page_path(request: HttpRequest) -> Path | None:
    """Return the resolved POST `_next_form_page` value, or `None`."""
    if not hasattr(request, "POST"):
        return None
    raw = request.POST.get("_next_form_page")
    if not raw or not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Path(raw.strip()).resolve()
    except OSError:
        return None


def validated_next_form_page_path(request: HttpRequest) -> Path | None:
    """Return a trusted `page.py` path from POST `_next_form_page`, or `None`.

    Accepts both real `page.py` files and virtual ones — directories whose
    only source is a sibling `template.djx`. The file router already emits
    routes for those. The downstream renderer tolerates a missing module
    and falls back to the template, so virtual pages survive the re-render
    path on form-validation failures.
    """
    p = _posted_page_path(request)
    if p is None or p.name != "page.py":
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


def _validated_origin_path(raw: object) -> str | None:
    """Return `raw` as a same-site path or `None`."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if not raw.startswith("/") or raw.startswith("//"):
        return None
    return raw


def redirect_to_origin(
    request: HttpRequest,
    fallback: str = "/",
) -> HttpResponseRedirect:
    """Redirect back to the page that rendered the form."""
    origin: str | None = None
    if hasattr(request, "POST"):
        origin = _validated_origin_path(request.POST.get("_next_form_origin"))
    return HttpResponseRedirect(origin or fallback)


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "URL_NAME_FORM_ACTION",
    "redirect_to_origin",
    "reverse_form_action",
    "validated_next_form_page_path",
]
