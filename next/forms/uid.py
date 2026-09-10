"""Dispatch-URL reversing, origin-path validation, and origin redirects."""

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


ORIGIN_FIELD_NAME = "_next_form_origin"

# Render-context key the shaping layer sets on a wizard advance to override the
# rendered form's _next_form_origin, instead of mutating a request attribute.
FORM_ORIGIN_OVERRIDE_KEY = "form_origin_override"

# Code points a browser removes from a URL before resolving it, per the WHATWG
# URL parser. Left in place they would hide a protocol-relative target.
_URL_DROPPED_CHARS = frozenset("\t\n\r")


def current_origin_path(request: HttpRequest) -> str | None:
    """Return the URL of `request` with its query string, or `None` without a path.

    The query rides along so a redirect back to the origin keeps the filters,
    the search terms, and the page the visitor was looking at. The path stays as
    Django decoded it rather than re-escaped, so a non-ASCII route still resolves
    against the URLconf on the way back.
    """
    path = getattr(request, "path", None)
    if not path:
        return None
    # A request whose META is not a real mapping is a unit-test stand-in, and
    # asking it for the query string would splice a stub into the field value.
    meta = getattr(request, "META", None)
    query = meta.get("QUERY_STRING", "") if isinstance(meta, dict) else ""
    return f"{path}?{query}" if query else str(path)


def validated_origin_path(raw: object) -> str | None:
    """Return `raw` as a same-site path or `None`.

    A tab or a newline anywhere is refused because a browser drops those code
    points before it resolves a URL, which would turn a value the check read as
    same-site into a protocol-relative jump off site.
    """
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if _URL_DROPPED_CHARS.intersection(raw):
        return None
    collapsed = raw.replace("\\", "/")
    if not raw.startswith("/") or collapsed.startswith("//"):
        return None
    return raw


def redirect_to_origin(
    request: HttpRequest, fallback: str = "/"
) -> HttpResponseRedirect:
    """Redirect back to the page that rendered the form."""
    origin: str | None = None
    if hasattr(request, "POST"):
        origin = validated_origin_path(request.POST.get(ORIGIN_FIELD_NAME))
    return HttpResponseRedirect(origin or fallback)


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "FORM_ORIGIN_OVERRIDE_KEY",
    "ORIGIN_FIELD_NAME",
    "URL_NAME_FORM_ACTION",
    "current_origin_path",
    "redirect_to_origin",
    "reverse_form_action",
    "validated_origin_path",
]
