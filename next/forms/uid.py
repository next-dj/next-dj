"""Dispatch-URL reversing, origin-path validation, and origin redirects."""

from contextlib import suppress

from django.core.exceptions import DisallowedRedirect
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils.encoding import escape_uri_path


URL_NAME_FORM_ACTION = "form_action"
FORM_ACTION_REVERSE_NAME = "next:form_action"


def reverse_form_action(uid: str) -> str:
    """Return the dispatch URL for a form action uid.

    The route reverses as `next:form_action` under the namespace, bare as `form_action`.
    """
    try:
        return reverse(FORM_ACTION_REVERSE_NAME, kwargs={"uid": uid})
    except NoReverseMatch:
        return reverse(URL_NAME_FORM_ACTION, kwargs={"uid": uid})


ORIGIN_FIELD_NAME = "_next_form_origin"

# Render-context key the shaping layer sets on a wizard advance to override the
# rendered form's _next_form_origin, instead of mutating a request attribute.
FORM_ORIGIN_OVERRIDE_KEY = "form_origin_override"

# Django's redirect Location cap, pinned because Django before 5.2.9 has no constant.
MAX_ORIGIN_LENGTH = 16384


def current_origin_path(request: HttpRequest) -> str | None:
    """Return the URL of `request` with its query string, or `None` without a path.

    Percent-encoded like `get_full_path`, so a `?` inside a segment stays in its path.
    """
    path = getattr(request, "path", None)
    if not path:
        return None
    # A request whose META is not a real mapping is a unit-test stand-in, and
    # asking it for the query string would splice a stub into the field value.
    meta = getattr(request, "META", None)
    query = meta.get("QUERY_STRING", "") if isinstance(meta, dict) else ""
    encoded = escape_uri_path(str(path))
    return f"{encoded}?{query}" if query else encoded


def is_path_only(candidate: str) -> bool:
    """Whether a browser resolves `candidate` against the current origin as a path.

    A backslash or a dropped code point would hide a protocol-relative target.
    """
    if not candidate.startswith("/") or candidate.startswith(("//", "/\\")):
        return False
    return not ("\t" in candidate or "\n" in candidate or "\r" in candidate)


def validated_origin_path(raw: object) -> str | None:
    """Return `raw` as a same-site path no longer than `MAX_ORIGIN_LENGTH`, or `None`.

    The length is read before the strip, so a megabyte value is refused without a scan.
    """
    if not isinstance(raw, str) or len(raw) > MAX_ORIGIN_LENGTH:
        return None
    candidate = raw.strip()
    return candidate if is_path_only(candidate) else None


def posted_origin_path(request: HttpRequest) -> str | None:
    """Return the validated origin a POST carries, or `None` for any other request."""
    if getattr(request, "method", None) != "POST":
        return None
    return validated_origin_path(request.POST.get(ORIGIN_FIELD_NAME))


def redirect_or_fallback(
    target: str, fallback: str, *, status: int = 302
) -> HttpResponseRedirect:
    """Redirect to `target`, or to `fallback` when Django refuses it as a Location.

    A handler has already run by now, so a target past the length cap is not a 400.
    """
    with suppress(DisallowedRedirect):
        return HttpResponseRedirect(target, status=status)
    return HttpResponseRedirect(fallback, status=status)


def redirect_to_origin(
    request: HttpRequest, fallback: str = "/"
) -> HttpResponseRedirect:
    """Redirect back to the page that rendered the form, or to `fallback`."""
    return redirect_or_fallback(posted_origin_path(request) or fallback, fallback)


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "FORM_ORIGIN_OVERRIDE_KEY",
    "MAX_ORIGIN_LENGTH",
    "ORIGIN_FIELD_NAME",
    "URL_NAME_FORM_ACTION",
    "current_origin_path",
    "is_path_only",
    "posted_origin_path",
    "redirect_or_fallback",
    "redirect_to_origin",
    "reverse_form_action",
    "validated_origin_path",
]
