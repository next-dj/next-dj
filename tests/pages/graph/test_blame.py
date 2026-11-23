from collections.abc import Awaitable, Callable

import pytest

from next.deps import Depends
from next.pages.graph import blame, blaming
from tests.support.graphs import LEAF, MID, ROOT, collect, failing


pytestmark = pytest.mark.usefixtures("framework_providers")


def _loops(value: object = None) -> object:
    return value


_loops.__defaults__ = (Depends(_loops),)


def _fails_async(message: str) -> Callable[[], Awaitable[str]]:
    async def fail() -> str:
        raise ValueError(message)

    return fail


class TestBlame:
    """A failed async collection names the inherited group the failure came from."""

    @pytest.mark.parametrize(("group", "level"), [(-1, -1), (0, 3), (1, 1), (2, -1)])
    def test_the_level_of_the_failed_group_reads_its_directory(
        self, group: int, level: int
    ) -> None:
        with blaming() as blamed:
            blame(group)

        assert blamed.level((3, 1)) == level

    def test_a_blame_outside_a_watch_is_dropped(self) -> None:
        blame(3)

        with blaming() as blamed:
            assert blamed.group == -1

    def test_a_failing_sync_step_of_a_serial_run_is_blamed(
        self, registry, inline
    ) -> None:
        async def own() -> str:
            return "own"

        registry.register_context(ROOT, "nav", failing("nav"), inherit_context=True)
        registry.register_context(LEAF, "own", own)

        with blaming() as blamed, pytest.raises(ValueError, match="nav"):
            collect(registry)

        assert blamed.group == 0

    def test_a_failing_async_step_of_a_serial_run_is_blamed(
        self, registry, inline
    ) -> None:
        registry.register_context(MID, "nav", _fails_async("nav"), inherit_context=True)

        with blaming() as blamed, pytest.raises(ValueError, match="nav"):
            collect(registry)

        assert blamed.group == 0

    def test_the_earliest_failure_of_a_parallel_run_is_blamed(
        self, registry, inline
    ) -> None:
        async def fine() -> str:
            return "fine"

        registry.register_context(LEAF, "a_fail", _fails_async("a"))
        registry.register_context(LEAF, "b_fine", fine)

        with blaming() as blamed, pytest.raises(ValueError, match="a"):
            collect(registry)

        assert registry.graph(LEAF).parallel
        assert blamed.group == -1
