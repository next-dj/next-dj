"""Read the body of a test response, whether Django built it at once or streamed it."""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from django.http.response import HttpResponseBase


def response_text(response: HttpResponseBase) -> str:
    """Return the body of a response as text, a streamed one read to its end."""
    if getattr(response, "streaming", False):
        body = b"".join(getattr(response, "streaming_content", ()))
    else:
        body = getattr(response, "content", b"")
    return body.decode(response.charset or "utf-8", errors="replace")


__all__ = ["response_text"]
