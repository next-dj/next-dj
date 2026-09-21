"""Dispatch-URL reversing, origin-path validation, and origin redirects."""

from django.core.exceptions import DisallowedHost
from django.http import HttpRequest, HttpResponseRedirect
from django.urls import reverse
from django.urls.exceptions import NoReverseMatch
from django.utils.http import url_has_allowed_host_and_scheme


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

_URL_DROPPED_CHARS = frozenset("\t\n\r")


def current_origin_path(request: HttpRequest) -> str | None:
    """Return the URL of `request` with its query string, or `None` without a path.

    The query rides along so a redirect back keeps the filters and the page. The path
    stays as Django decoded it, so a non-ASCII route still resolves on the way back.
    """
    path = getattr(request, "path", None)
    if not path:
        return None
    # A request whose META is not a real mapping is a unit-test stand-in, and
    # asking it for the query string would splice a stub into the field value.
    meta = getattr(request, "META", None)
    query = meta.get("QUERY_STRING", "") if isinstance(meta, dict) else ""
    return f"{path}?{query}" if query else str(path)


def _allowed_hosts(request: HttpRequest) -> set[str] | None:
    """Return the single host a same-site target may name, if the request has one.

    A request built in code carries no host and a refused one must not become an
    allowance, so `None` answers for it, which lets no absolute target through.
    """
    try:
        return {request.get_host()}
    except (KeyError, DisallowedHost):
        return None


def _is_path_only(candidate: str) -> bool:
    """Whether a browser resolves `candidate` against the current origin as a path.

    A backslash or a dropped code point would hide a protocol-relative target.
    """
    if _URL_DROPPED_CHARS.intersection(candidate):
        return False
    collapsed = candidate.replace("\\", "/")
    return candidate.startswith("/") and not collapsed.startswith("//")


def validated_origin_path(raw: object, *, request: HttpRequest) -> str | None:
    """Return `raw` as a same-site path or `None`.

    Django's helper rules on host and scheme first, so an open-redirect fix lands here
    too, and the path-only policy on top refuses even an absolute URL naming this host.
    """
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts=_allowed_hosts(request),
        require_https=request.is_secure(),
    ):
        return None
    return candidate if _is_path_only(candidate) else None


def redirect_to_origin(
    request: HttpRequest, fallback: str = "/"
) -> HttpResponseRedirect:
    """Redirect back to the page that rendered the form."""
    origin: str | None = None
    if hasattr(request, "POST"):
        origin = validated_origin_path(
            request.POST.get(ORIGIN_FIELD_NAME), request=request
        )
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
