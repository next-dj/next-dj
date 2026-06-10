"""UID generation and redirect helpers for form actions."""

from pathlib import Path

from django.conf import settings
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch


URL_NAME_FORM_ACTION = "form_action"
FORM_ACTION_REVERSE_NAME = "next:form_action"


# Memoised by the raw BASE_DIR string: resolve() hits the filesystem on every
# error re-render. No invalidation needed since resolve() is process-stable
# per path string, and a changed BASE_DIR yields a different key.
_resolved_base_dirs: dict[str, Path] = {}


def _resolved_base_dir(base: object) -> Path:
    """Return `base` resolved to an absolute path, memoised by its string form."""
    key = str(base)
    resolved = _resolved_base_dirs.get(key)
    if resolved is None:
        resolved = Path(key).resolve()
        _resolved_base_dirs[key] = resolved
    return resolved


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


def page_path_token(file_path: str) -> str:
    """Return the client-facing token for a page module path.

    The token is the path relative to BASE_DIR, so rendered HTML never
    exposes the server filesystem layout. Falls back to the raw path
    when BASE_DIR is unset or does not contain the file.
    """
    base = getattr(settings, "BASE_DIR", None)
    if base is None:
        return file_path
    try:
        return str(Path(file_path).resolve().relative_to(_resolved_base_dir(base)))
    except (OSError, ValueError):
        return file_path


def _posted_page_path(request: HttpRequest) -> Path | None:
    """Return the resolved POST `_next_form_page` value, or `None`.

    Relative values resolve against BASE_DIR, matching the token the
    form tag emits.
    """
    if not hasattr(request, "POST"):
        return None
    raw = request.POST.get("_next_form_page")
    if not raw or not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw.strip())
    if not candidate.is_absolute():
        base = getattr(settings, "BASE_DIR", None)
        if base is None:
            return None
        candidate = _resolved_base_dir(base) / candidate
    try:
        return candidate.resolve()
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
        p.relative_to(_resolved_base_dir(base))
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
    "page_path_token",
    "redirect_to_origin",
    "reverse_form_action",
    "validated_next_form_page_path",
]
