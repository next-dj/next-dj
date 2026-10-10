"""Read the body of a test response, whether Django built it at once or streamed it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.http import HttpResponse, StreamingHttpResponse


if TYPE_CHECKING:
    from django.http.response import HttpResponseBase


def response_text(response: HttpResponseBase) -> str:
    """Return the body of a response as text, a streamed one read to its end.

    A streamed body is buffered back onto the response, so a second read sees it too.
    """
    body = b""
    if isinstance(response, StreamingHttpResponse):
        body = response.getvalue()
        response.streaming_content = (body,)
    elif isinstance(response, HttpResponse):
        body = response.content
    return body.decode(response.charset or "utf-8", errors="replace")


__all__ = ["response_text"]
