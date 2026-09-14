"""Reading the CSRF rotation marker and stamping the refreshed payload."""

from typing import TYPE_CHECKING

from next.static.scripts import csrf_payload


if TYPE_CHECKING:
    from django.http import HttpRequest

    from next.partial.patches import Patches


_CSRF_ROTATED_FLAG = "CSRF_COOKIE_NEEDS_UPDATE"


def _csrf_rotated(request: "HttpRequest") -> bool:
    """Return True when the request rotated its CSRF token.

    Django flags a rotated token on `request.META`, so the marker is read before a
    fresh render mints one. A META that is no mapping reads unrotated.
    """
    meta = getattr(request, "META", None)
    if not isinstance(meta, dict):
        return False
    return bool(meta.get(_CSRF_ROTATED_FLAG))


def _stamp_csrf(request: "HttpRequest", patches: "Patches", *, rotated: bool) -> None:
    """Attach the rotated CSRF payload when the request rotated its token."""
    if rotated:
        patches.set_csrf(csrf_payload(request))
