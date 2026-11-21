"""The request scope one async render of a page resolves its dependencies in."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from next.deps.cache import RequestMemo, request_memo
from next.deps.scope import RequestScope


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping


@asynccontextmanager
async def render_scope(seed: Mapping[str, object]) -> AsyncIterator[RequestScope]:
    """Open the request scope of one async render and close it once the render ends.

    It is seeded from the shell cache and shared through the request memo.
    """
    scope = RequestScope(seed)
    token = request_memo.set(RequestMemo(scope))
    try:
        yield scope
    finally:
        request_memo.reset(token)
        await scope.close()


__all__ = ["render_scope"]
