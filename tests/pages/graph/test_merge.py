import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from next.pages import PageContextShapeError
from next.pages.graph import ContextGraph, acollect, merged
from next.pages.registry import PageContextRegistry


ROOT = Path("/graph/page.py")
MID = Path("/graph/mid/page.py")
LEAF = Path("/graph/mid/leaf/page.py")

pytestmark = pytest.mark.usefixtures("framework_providers")


def _collect(registry: PageContextRegistry, file_path: Path = LEAF, **kwargs):
    """Run `acollect_context` of `file_path` on a fresh loop."""
    return asyncio.run(registry.acollect_context(file_path, **kwargs))


def _run_graph(graph: ContextGraph, file_path: Path = LEAF, **kwargs):
    """Run `graph` through `acollect` on a fresh loop with empty inputs."""
    options = {
        "request": None,
        "url_kwargs": {},
        "dep_cache": {},
        "requested_zones": None,
        **kwargs,
    }
    return asyncio.run(acollect(graph, file_path, **options))


class TestMerge:
    """The results of a run merge in run order, an inherited key taken once."""

    @pytest.mark.parametrize(
        ("file_path", "inherit", "sibling"),
        [
            pytest.param(ROOT, True, False, id="inherited"),
            pytest.param(LEAF, False, False, id="own"),
            pytest.param(LEAF, False, True, id="beside-a-sibling"),
        ],
    )
    def test_a_keyless_async_callable_must_return_a_mapping(
        self, registry, inline, file_path: Path, *, inherit: bool, sibling: bool
    ) -> None:
        async def spread() -> list[int]:
            return [1]

        async def two() -> int:
            return 2

        registry.register_context(file_path, None, spread, inherit_context=inherit)
        if sibling:
            registry.register_context(LEAF, "two", two)

        with pytest.raises(
            PageContextShapeError,
            match=r"keyless `@context` spread returned a non-mapping",
        ):
            _collect(registry)

    @pytest.mark.parametrize("colour", ["async", "sync"])
    def test_an_inherited_key_already_taken_skips_its_later_callable(
        self, registry, inline, colour: str
    ) -> None:
        called: list[str] = []

        async def again_async() -> str:
            called.append("again")
            return "mid"

        def again_sync() -> str:
            called.append("again")
            return "mid"

        async def first_async() -> str:
            return "root"

        def first_sync() -> str:
            return "root"

        first, again = (
            (first_sync, again_async)
            if colour == "async"
            else (first_async, again_sync)
        )
        registry.register_context(ROOT, "k", first, inherit_context=True)
        registry.register_context(MID, "k", again, inherit_context=True)

        assert _collect(registry).context_data == {"k": "root"}
        assert called == []

    def test_the_merge_equals_the_sequential_run(self, registry, inline) -> None:
        async def one() -> dict[str, int]:
            await asyncio.sleep(0.002)
            return {"one": 1, "shared": 1}

        async def two() -> str:
            return "two"

        registry.register_context(LEAF, None, one, serialize=True)
        registry.register_context(LEAF, "shared", two, serialize=True)
        registry.register_context(LEAF, "three", lambda: 3)
        graph = registry.graph(LEAF)

        parallel = _run_graph(graph)
        serial = _run_graph(replace(graph, parallel=False))

        assert graph.parallel is True
        assert parallel == serial
        assert list(parallel.context_data) == list(serial.context_data)

    def test_the_results_merge_in_run_order(self, registry) -> None:
        registry.register_context(MID, "nav", lambda: "mid", inherit_context=True)
        registry.register_context(LEAF, "nav", lambda: "own")
        graph = registry.graph(LEAF)

        result = merged(graph, LEAF, {1: "own", 0: "mid"})

        assert result.context_data == {"nav": "own"}
