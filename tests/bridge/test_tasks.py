import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import partial

import pytest

from next.bridge.tasks import StealableTask, contextless
from tests.support.errors import HaltError
from tests.support.waits import WAIT


def _fail() -> None:
    msg = "work failed"
    raise ValueError(msg)


def _halt() -> None:
    raise HaltError


def _claim_after(barrier: threading.Barrier, task: StealableTask[object]) -> bool:
    barrier.wait()
    return task.run()


_mark: contextvars.ContextVar[str] = contextvars.ContextVar("mark")


class TestContextless:
    """The call runs in an empty context and hands back what it returns."""

    def test_the_call_sees_nothing_of_the_caller_and_returns_its_result(self) -> None:
        def scenario() -> tuple[str, str]:
            _mark.set("caller")
            return contextless(_mark.get, "unset"), _mark.get()

        assert contextvars.Context().run(scenario) == ("unset", "caller")


class TestStealableTask:
    """Work runs at most once, on whichever caller claims it first."""

    def test_exactly_one_racing_caller_claims_the_work(self) -> None:
        runs: list[int] = []
        for _ in range(50):
            task: StealableTask[object] = StealableTask(partial(runs.append, 1))
            barrier = threading.Barrier(8, timeout=WAIT)
            with ThreadPoolExecutor(8) as pool:
                racers = [pool.submit(_claim_after, barrier, task) for _ in range(8)]
                claimed = [racer.result(WAIT) for racer in racers]
            assert claimed.count(True) == 1
        assert len(runs) == 50

    def test_the_result_settles_the_future_once(self) -> None:
        calls: list[int] = []
        task = StealableTask(lambda: calls.append(1) or len(calls))
        assert task.run() is True
        assert task.run() is False
        assert task.reject(RuntimeError("late")) is False
        assert task.future.result(0) == 1
        assert calls == [1]

    def test_a_rejected_task_never_runs(self) -> None:
        calls: list[int] = []
        task = StealableTask(lambda: calls.append(1))
        error = RuntimeError("gone")
        assert task.reject(error) is True
        assert task.run() is False
        assert task.future.exception(0) is error
        assert calls == []

    def test_a_cancelled_task_is_skipped(self) -> None:
        calls: list[int] = []
        task = StealableTask(lambda: calls.append(1))
        assert task.future.cancel()
        assert task.run() is False
        assert task.claim() is False
        assert calls == []

    def test_the_claim_hook_fires_once_when_a_caller_runs_the_work(self) -> None:
        claims: list[bool] = []
        task = StealableTask(lambda: claims.append(task.future.running()))
        task.when_claimed(lambda: claims.append(False))
        assert task.run() is True
        assert task.run() is False
        assert claims == [False, True]

    def test_the_claim_hook_fires_for_work_claimed_before_it_was_set(self) -> None:
        claims: list[int] = []
        task = StealableTask(lambda: None)
        task.run()
        task.when_claimed(lambda: claims.append(1))
        assert claims == [1]

    def test_the_claim_hook_fires_once_for_a_cancelled_future(self) -> None:
        claims: list[int] = []
        task = StealableTask(lambda: None)
        task.when_claimed(lambda: claims.append(1))
        assert task.future.cancel()
        assert task.claim() is False
        assert claims == [1]

    def test_racing_claims_fire_the_hook_exactly_once(self) -> None:
        claims: list[int] = []
        task: StealableTask[object] = StealableTask(lambda: None)
        task.when_claimed(lambda: claims.append(1))
        barrier = threading.Barrier(8, timeout=WAIT)
        with ThreadPoolExecutor(8) as pool:
            racers = [pool.submit(_claim_after, barrier, task) for _ in range(8)]
            assert [racer.result(WAIT) for racer in racers].count(True) == 1
        assert claims == [1]

    def test_an_error_settles_the_future_and_stays_with_it(self) -> None:
        task = StealableTask(_fail)
        assert task.run() is True
        assert isinstance(task.future.exception(0), ValueError)

    def test_an_interrupt_fails_the_future_and_reaches_the_runner(self) -> None:
        task = StealableTask(_halt)
        with pytest.raises(HaltError):
            task.run()
        error = task.future.exception(0)
        assert isinstance(error, RuntimeError)
        assert isinstance(error.__cause__, HaltError)
