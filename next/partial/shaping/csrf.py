"""Detection of a rotated CSRF token and the new payload a patch carries for it."""

from typing import TYPE_CHECKING

from next.csrf import csrf_token_payload


if TYPE_CHECKING:
    from django.http import HttpRequest

    from next.partial.patches import Patches


_CSRF_ROTATED_FLAG = "CSRF_COOKIE_NEEDS_UPDATE"


def _csrf_rotated(request: "HttpRequest") -> bool:
    """Return True when the request rotated its CSRF token.

    Django sets the flag in `request.META`, so it is read before a render creates a
    new token. A `META` that is not a dict reads as not rotated.
    """
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return False
    return bool(meta.get(_CSRF_ROTATED_FLAG))


def _stamp_csrf(request: "HttpRequest", patches: "Patches", *, rotated: bool) -> None:
    """Attach the rotated CSRF payload when the request rotated its token."""
    if rotated:
        patches.set_csrf(csrf_token_payload(request))
