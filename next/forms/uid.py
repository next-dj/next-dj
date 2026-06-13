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


def validated_origin_path(raw: object) -> str | None:
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
        origin = validated_origin_path(request.POST.get(ORIGIN_FIELD_NAME))
    return HttpResponseRedirect(origin or fallback)


__all__ = [
    "FORM_ACTION_REVERSE_NAME",
    "ORIGIN_FIELD_NAME",
    "URL_NAME_FORM_ACTION",
    "redirect_to_origin",
    "reverse_form_action",
    "validated_origin_path",
]
