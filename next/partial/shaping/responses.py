"""Serialisation of a finished builder into a partial response."""

from typing import TYPE_CHECKING

from next.partial.manager import partial_backend_manager
from next.partial.patches import PatchResponse

from .csrf import _stamp_csrf


if TYPE_CHECKING:
    from django.http import HttpRequest

    from next.partial.patches import Patches


def _envelope_response(
    patches: "Patches", *, request: "HttpRequest | None" = None, rotated: bool = False
) -> PatchResponse:
    """Serialise the builder's envelope into a partial response.

    A rotated CSRF token is stamped here so every outcome carries it, not just validate.
    """
    if request is not None and rotated:
        _stamp_csrf(request, patches, rotated=rotated)
    backend_obj = partial_backend_manager.get()
    body = backend_obj.serialize_envelope(patches.envelope())
    return PatchResponse(
        body, content_type=backend_obj.content_type, version=patches.version
    )
