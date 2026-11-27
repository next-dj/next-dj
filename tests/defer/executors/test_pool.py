import contextvars
import logging
import os
import subprocess
import sys
import threading
import time
import weakref
from concurrent.futures import wait
from pathlib import Path

import pytest

from next.bridge.tasks import StealableTask
from next.conf.defer import defer_count
from next.defer import Capacity
from next.defer.errors import BoundaryShedError, ExecutorStoppedError
from next.defer.executors import LaneExecutor, pool, retire_thread
from next.defer.executors.pool import _forget_pools, _stop_at_exit, _Worker
from next.testing import override_next_settings
from tests.bridge.shared import interrupt
from tests.defer.executors.shared import Clock, blocked, pool_threads
from tests.django_setup import PROJECT_ROOT
from tests.support.errors import fail_hop
from tests.support.starts import Request, held, spied_starts
from tests.support.waits import WAIT, until


class TestLaneExecutorThreads:
    """``LaneExecutor`` starts threads on demand, up to its cap."""

    def test_no_thread_exists_before_the_first_submit(self, pools) -> None:
        before = set(threading.enumerate())
        executor = pools()
        assert set(threading.enumerate()) <= before
        assert executor.submit(StealableTask(lambda: 42)).result(WAIT) == 42
        started = set(threading.enumerate()) - before
        assert len(started) == 1
        assert all(thread.name.startswith("next-pool-") for thread in started)
        assert all(thread.daemon for thread in started)

    def test_a_worker_starts_outside_the_context_of_the_submitter(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = spied_starts(monkeypatch)
        executor = pools()

        def submit() -> int:
            held.set(Request())
            return executor.submit(StealableTask(lambda: 42)).result(WAIT)

        assert contextvars.Context().run(submit) == 42
        assert seen == [None]

    def test_an_idle_worker_takes_the_next_task_without_a_new_thread(
        self, pools
    ) -> None:
        before = pool_threads()
        executor = pools()
        executor.submit(StealableTask(lambda: 1)).result(WAIT)
        until(lambda: executor.capacity().busy == 0)
        executor.submit(StealableTask(lambda: 2)).result(WAIT)
        assert len(pool_threads() - before) == 1

    def test_workers_stop_at_the_cap(self, pools) -> None:
        before = pool_threads()
        executor = pools(max_workers=2)
        gate = threading.Event()
        futures = [executor.submit(blocked(gate)) for _ in range(4)]
        until(lambda: executor.capacity().busy == 2)
        capacity = executor.capacity()
        assert (capacity.workers, capacity.busy, capacity.queued) == (2, 2, 2)
        assert capacity.idle == 0
        assert len(pool_threads() - before) == 2
        gate.set()
        assert all(future.result(WAIT) for future in futures)

    def test_a_full_queue_sheds_without_blocking(self, pools) -> None:
        executor = pools(max_workers=1, max_queue=2)
        gate = threading.Event()
        executor.submit(blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        queued = [executor.submit(StealableTask(lambda: 0)) for _ in range(2)]
        with pytest.raises(BoundaryShedError):
            executor.submit(StealableTask(lambda: 0))
        assert executor.capacity().queued == 2
        gate.set()
        assert [future.result(WAIT) for future in queued] == [0, 0]

    def test_sizes_the_options_omit_come_from_the_settings(self) -> None:
        executor = LaneExecutor()
        assert executor.max_workers == defer_count("EXECUTOR", "OPTIONS", "MAX_WORKERS")
        assert executor.max_queue == defer_count("EXECUTOR", "OPTIONS", "MAX_QUEUE")
        assert (executor.conn_idle, executor.shutdown_timeout) == (60.0, 5.0)
        options = {"MAX_WORKERS": 3, "CONN_IDLE": "2s", "SHUTDOWN_TIMEOUT": "1s"}
        with override_next_settings(DEFER={"EXECUTOR": {"OPTIONS": options}}):
            executor = LaneExecutor({"OPTIONS": {"MAX_QUEUE": 5}})
        assert (executor.max_workers, executor.max_queue) == (3, 5)
        assert (executor.conn_idle, executor.shutdown_timeout) == (2.0, 1.0)


class TestLaneExecutorOutcomes:
    """Results and failures reach the future, and no failure ends a worker."""

    def test_an_exception_reaches_the_future(self, pools) -> None:
        future = pools().submit(StealableTask(fail_hop))
        with pytest.raises(ValueError, match="hop failed"):
            future.result(WAIT)

    def test_an_interrupt_reaches_the_future_and_spares_the_worker(
        self, pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        before = pool_threads()
        executor = pools(max_workers=1)
        with caplog.at_level(logging.ERROR, logger="next.defer"):
            error = executor.submit(StealableTask(interrupt)).exception(5)
            until(lambda: executor.capacity().busy == 0)
        assert isinstance(error, RuntimeError)
        assert isinstance(error.__cause__, KeyboardInterrupt)
        assert "raised an interrupt" in caplog.text
        assert executor.submit(StealableTask(lambda: "alive")).result(WAIT) == "alive"
        assert len(pool_threads() - before) == 1


class TestLaneExecutorSteal:
    """A waiter runs a queued task itself, and the worker skips it."""

    def test_a_waiter_runs_a_queued_task_and_the_gauges_follow(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = Clock()
        monkeypatch.setattr(pool, "time", clock)
        executor = pools(max_workers=1)
        gate = threading.Event()
        executor.submit(blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        ran: dict[str, threading.Thread] = {}

        def recorded(name: str) -> StealableTask[str]:
            def run() -> str:
                ran[name] = threading.current_thread()
                return name

            return StealableTask(run)

        first, middle, last = recorded("a"), recorded("b"), recorded("c")
        executor.submit(first)
        clock.now += 3.0
        executor.submit(middle)
        executor.submit(last)
        assert executor.steal(middle) is True
        assert executor.steal(middle) is False
        assert ran["b"] is threading.current_thread()
        clock.now += 1.0
        capacity = executor.capacity()
        assert capacity.queued == 2
        assert capacity.delay == 4.0
        assert executor.steal(first) is True
        capacity = executor.capacity()
        assert capacity.queued == 1
        assert capacity.delay == 1.0
        gate.set()
        assert last.future.result(WAIT) == "c"
        assert ran["c"].name.startswith("next-pool-")
        assert [first.future.result(0), middle.future.result(0)] == ["a", "b"]
        until(lambda: executor.capacity() == Capacity(1, 0, 0, 0.0))

    def test_a_task_run_directly_leaves_the_queue_at_once(self, pools) -> None:
        executor = pools(max_workers=1, max_queue=1)
        gate = threading.Event()
        executor.submit(blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        direct = StealableTask(lambda: "direct")
        executor.submit(direct)
        assert direct.run() is True
        assert executor.capacity().queued == 0
        after = executor.submit(StealableTask(lambda: "after"))
        gate.set()
        assert after.result(WAIT) == "after"
        assert direct.future.result(0) == "direct"

    def test_a_task_never_queued_here_is_not_stolen(self, pools) -> None:
        task = StealableTask(lambda: 1)
        assert pools().steal(task) is False
        assert not task.future.done()

    def test_many_stealers_and_workers_run_each_task_once(self, pools) -> None:
        executor = pools(max_workers=4, max_queue=400)
        runs = [0] * 400
        lock = threading.Lock()

        def counted(index: int) -> StealableTask[int]:
            def run() -> int:
                with lock:
                    runs[index] += 1
                time.sleep(0)
                return index

            return StealableTask(run)

        tasks = [counted(index) for index in range(400)]
        barrier = threading.Barrier(9, timeout=WAIT)

        def steal(order: list[StealableTask[int]]) -> None:
            barrier.wait(WAIT)
            for task in order:
                executor.steal(task)

        stealers = [
            threading.Thread(
                target=steal, args=(tasks[:: -1 if index % 2 else 1],), daemon=True
            )
            for index in range(8)
        ]
        for stealer in stealers:
            stealer.start()
        for task in tasks:
            executor.submit(task)
        barrier.wait(WAIT)
        for stealer in stealers:
            stealer.join(WAIT)
        assert not any(stealer.is_alive() for stealer in stealers)
        done, _ = wait([task.future for task in tasks], timeout=WAIT)
        assert len(done) == 400
        assert runs == [1] * 400
        assert [task.future.result(0) for task in tasks] == list(range(400))
        until(lambda: executor.capacity() == Capacity(4, 0, 0, 0.0))

    def test_capacity_rereads_a_queue_that_changed_under_it(self, pools) -> None:
        class Shifting(dict):
            moved = False

            def values(self):
                if not self.moved:
                    self.moved = True
                    msg = "dictionary changed size during iteration"
                    raise RuntimeError(msg)
                return super().values()

        executor = pools()
        executor._pending = Shifting({StealableTask(lambda: 1): time.monotonic()})
        capacity = executor.capacity()
        assert capacity.queued == 1
        assert capacity.delay >= 0.0
        executor._pending = {}


class TestLaneExecutorStop:
    """Shutdown, retirement and the fork hook."""

    def test_a_task_that_retires_its_thread_leaves_the_next_to_a_new_one(
        self, pools
    ) -> None:
        executor = pools(max_workers=1)

        def retiring() -> threading.Thread:
            retire_thread()
            return threading.current_thread()

        retired = executor.submit(StealableTask(retiring)).result(WAIT)
        retired.join(WAIT)
        assert not retired.is_alive()
        current = StealableTask(threading.current_thread)
        assert executor.submit(current).result(WAIT) is not retired

    def test_retiring_a_thread_no_pool_owns_does_nothing(self) -> None:
        retire_thread()
        assert threading.current_thread().is_alive()

    def test_shutdown_fails_queued_tasks_and_lets_running_ones_finish(
        self, pools
    ) -> None:
        before = pool_threads()
        executor = pools(max_workers=1)
        gate = threading.Event()
        running = executor.submit(StealableTask(lambda: gate.wait(WAIT) and "finished"))
        until(lambda: executor.capacity().busy == 1)
        queued = executor.submit(StealableTask(lambda: "never"))
        queued.add_done_callback(lambda _: gate.set())
        executor.shutdown(5)
        assert running.result(0) == "finished"
        assert isinstance(queued.exception(0), ExecutorStoppedError)
        assert not pool_threads() - before
        with pytest.raises(ExecutorStoppedError):
            executor.submit(StealableTask(lambda: 0))

    def test_shutdown_without_a_timeout_waits_its_shutdown_timeout(self, pools) -> None:
        executor = pools(max_workers=1, shutdown_timeout=0.05)
        gate = threading.Event()
        running = executor.submit(blocked(gate))
        until(lambda: executor.capacity().busy == 1)
        started = time.monotonic()
        executor.shutdown()
        assert time.monotonic() - started < WAIT
        assert not running.done()
        gate.set()
        assert running.result(WAIT)

    @pytest.mark.parametrize(("grace", "left"), [("5", ["finished"]), ("0", [])])
    def test_an_exiting_process_gives_a_late_job_its_shutdown_timeout(
        self, tmp_path: Path, grace: str, left: list[str]
    ) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import os, threading, time\n"
                    "from pathlib import Path\n"
                    "from django.conf import settings\n"
                    "settings.configure()\n"
                    "from next.defer.executors import LaneExecutor\n"
                    "from next.bridge.tasks import StealableTask\n"
                    "started = threading.Event()\n"
                    "def late():\n"
                    "    started.set()\n"
                    "    time.sleep(0.3)\n"
                    "    Path(os.environ['EXIT_MARKER']).write_text('finished')\n"
                    "grace = float(os.environ['EXIT_GRACE'])\n"
                    "pool = LaneExecutor({'OPTIONS': {'SHUTDOWN_TIMEOUT': grace}})\n"
                    "pool.submit(StealableTask(late))\n"
                    "started.wait(5)\n"
                ),
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            env={**os.environ, "EXIT_MARKER": str(tmp_path / "m"), "EXIT_GRACE": grace},
        )
        assert completed.returncode == 0, completed.stderr
        assert [found.read_text() for found in tmp_path.iterdir()] == left

    def test_the_exit_hook_stops_every_pool_and_then_the_loop(
        self, pools, loop_owner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        executor = pools(shutdown_timeout=5)
        monkeypatch.setattr(pool, "_live", weakref.WeakSet([executor]))
        monkeypatch.setattr(pool, "background_loop", loop_owner)
        loop = loop_owner.get()
        order: list[str] = []
        executor.submit(StealableTask(lambda: order.append("job")))
        _stop_at_exit()
        order.append("stopped")
        assert order == ["job", "stopped"]
        assert loop.is_closed()
        with pytest.raises(ExecutorStoppedError):
            executor.submit(StealableTask(lambda: 0))

    def test_an_idle_worker_retires_and_a_later_submit_starts_one(self, pools) -> None:
        before = pool_threads()
        executor = pools(max_workers=1, conn_idle=0.01)
        executor.linger = 0.02
        executor.submit(StealableTask(lambda: 1)).result(WAIT)
        started = pool_threads() - before
        for thread in started:
            thread.join(WAIT)
        assert not any(thread.is_alive() for thread in started)
        assert not pool_threads() - before
        assert executor.submit(StealableTask(lambda: 2)).result(WAIT) == 2

    def test_a_worker_stays_while_queued_tasks_need_it(self, pools) -> None:
        executor = pools(max_workers=1)
        worker = _Worker(lambda _: None, "next-pool-test")
        executor._workers = (worker,)
        executor._pending[StealableTask(lambda: 1)] = time.monotonic()
        assert executor._retire(worker) is False
        assert executor._workers == (worker,)
        executor._pending.clear()
        assert executor._retire(worker) is True
        assert executor._workers == ()

    def test_a_stopped_pool_starts_no_worker(self, pools) -> None:
        executor = pools(max_workers=1)
        executor._pending[StealableTask(lambda: 1)] = time.monotonic()
        executor._stopped = True
        executor._staff()
        assert executor._workers == ()

    def test_the_fork_hook_drops_inherited_workers_and_queue(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = pool_threads()
        executor = pools(max_workers=1, conn_idle=0.01)
        executor.linger = 0.01
        monkeypatch.setattr(pool, "_live", weakref.WeakSet([executor]))
        gate = threading.Event()
        executor.submit(blocked(gate))
        executor.submit(StealableTask(lambda: 1))
        until(lambda: executor.capacity().busy == 1)
        inherited = list(pool_threads() - before)
        _forget_pools()
        assert executor.capacity() == Capacity(1, 0, 0, 0.0)
        assert executor.submit(StealableTask(lambda: "fresh")).result(WAIT) == "fresh"
        gate.set()
        for thread in inherited:
            thread.join(WAIT)
        assert not any(thread.is_alive() for thread in inherited)
