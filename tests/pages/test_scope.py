import asyncio

import pytest

from next.deps.cache import request_memo
from next.deps.scope import RequestScope
from next.pages.scope import render_scope


async def _answer() -> int:
    return 42


def _reuse(scope: RequestScope) -> object:
    """Ask `scope` for a key it never held, which a closed scope refuses."""

    async def main() -> object:
        return await scope.resolve("key", _answer)

    return asyncio.run(main())


class TestRenderScope:
    """One async render opens a request scope seeded from the shell cache."""

    def test_the_scope_is_the_memo_of_the_render_and_closes_after(self) -> None:
        async def main() -> tuple[object, RequestScope, object, object]:
            async with render_scope({"user": "ada"}) as scope:
                memo = request_memo.get()
                seeded = scope.get("user")
            return memo.scope, scope, seeded, request_memo.get()

        memo_scope, scope, seeded, after = asyncio.run(main())

        assert memo_scope is scope
        assert seeded == "ada"
        assert after is None
        with pytest.raises(RuntimeError, match="closed"):
            _reuse(scope)

    def test_the_scope_closes_when_the_render_fails(self) -> None:
        scopes: list[RequestScope] = []

        async def main() -> None:
            async with render_scope({}) as scope:
                scopes.append(scope)
                msg = "render failed"
                raise ValueError(msg)

        with pytest.raises(ValueError, match="render failed"):
            asyncio.run(main())
        with pytest.raises(RuntimeError, match="closed"):
            _reuse(scopes[0])
