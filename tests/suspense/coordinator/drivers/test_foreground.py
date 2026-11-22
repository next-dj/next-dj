import asyncio
import threading
import time
from collections.abc import Callable

import pytest
from django.test import override_settings

from next.suspense.coordinator import Foreground, foreground
from tests.suspense.coordinator.drivers.doubles import WAIT


class _Interleaved:
    """A lock that runs `between` once, right after its next release."""

    between: Callable[[], object] | None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.between = None

    def __enter__(self) -> bool:
        return self._lock.acquire()

    def __exit__(self, *exc: object) -> None:
        self._lock.release()
        between, self.between = self.between, None
        if between is not None:
            between()


class TestForeground:
    """Tests for ``Foreground`` and the process permits."""

    def test_a_release_beside_a_queueing_waiter_reaches_it(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        lock = _Interleaved()
        permits._lock = lock

        async def scenario() -> bool:
            lock.between = permits.release
            return await permits.wait(time.monotonic() + WAIT)

        assert asyncio.run(scenario())
        assert not permits.take()

    def test_take_spends_the_free_permits(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        assert not permits.take()
        permits.release()
        assert permits.take()

    def test_wait_returns_at_once_with_a_free_permit(self) -> None:
        permits = Foreground(1)
        assert asyncio.run(permits.wait(time.monotonic()))

    def test_wait_gives_up_at_its_deadline(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        assert not asyncio.run(permits.wait(time.monotonic() + 0.02))
        permits.release()
        assert permits.take()

    def test_a_released_permit_reaches_the_oldest_waiter(self) -> None:
        permits = Foreground(1)
        assert permits.take()

        async def scenario() -> bool:
            waiting = asyncio.ensure_future(permits.wait(time.monotonic() + WAIT))
            await asyncio.sleep(0)
            permits.release()
            return await waiting

        assert asyncio.run(scenario())
        assert not permits.take()

    def test_a_cancelled_waiter_leaves_without_a_permit(self) -> None:
        permits = Foreground(1)
        assert permits.take()

        async def scenario() -> None:
            waiting = asyncio.ensure_future(permits.wait(time.monotonic() + WAIT))
            await asyncio.sleep(0)
            waiting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiting

        asyncio.run(scenario())
        assert not permits.take()
        permits.release()
        assert permits.take()

    def test_a_granted_waiter_that_left_hands_its_permit_back(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        loop = asyncio.new_event_loop()
        try:
            granted: asyncio.Future[None] = loop.create_future()
            granted.set_result(None)
            permits._leave((loop, granted))
        finally:
            loop.close()
        assert permits.take()

    def test_a_waiter_on_a_closed_loop_is_skipped(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        loop = asyncio.new_event_loop()
        stale: asyncio.Future[None] = loop.create_future()
        loop.close()
        permits._waiters.append((loop, stale))
        permits.release()
        assert not permits._waiters
        assert permits.take()

    def test_a_grant_to_a_waiter_that_left_frees_the_permit(self) -> None:
        permits = Foreground(1)
        assert permits.take()

        async def scenario() -> None:
            gone: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            gone.cancel()
            permits._grant(gone)

        asyncio.run(scenario())
        assert permits.take()

    def test_the_process_permits_follow_max_foreground(self) -> None:
        options = {"EXECUTOR": {"OPTIONS": {"MAX_FOREGROUND": 3}}}
        with override_settings(NEXT_FRAMEWORK={"DEFER": options}):
            held = foreground()
            assert held.limit == 3
            assert foreground() is held
