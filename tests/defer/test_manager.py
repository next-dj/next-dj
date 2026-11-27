import threading
from collections.abc import Callable, Iterator
from concurrent.futures import Future
from typing import Any
from unittest import mock

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.bridge.tasks import StealableTask
from next.defer import (
    Admit,
    BoundaryExecutor,
    Capacity,
    DefaultDeliveryPolicy,
    DefaultServerProbe,
    QueueDelay,
    defer_backend_loaded,
)
from next.defer.errors import ExecutorStoppedError
from next.defer.executors import LaneExecutor
from next.defer.manager import DeferManager, defer_manager, forget_defer_backends
from next.defer.tokens import StackedTokenBackend
from next.defer.tokens.carrier import QueryCarrier
from next.errors import BackendImportError
from next.testing import capture_signals, override_next_settings
from tests.support.waits import WAIT


class AlwaysRequest(QueueDelay):
    """An admission subclass a settings entry can name."""

    def admit(self, capacity: Capacity) -> Admit:
        return Admit.REQUEST


class InlineExecutor(BoundaryExecutor):
    """An executor a settings entry can name, running each task as it is queued."""

    stopped: float | None = None

    def submit[R](self, task: StealableTask[R]) -> Future[R]:
        task.run()
        return task.future

    def steal(self, task: StealableTask[Any]) -> bool:
        return False

    def capacity(self) -> Capacity:
        return Capacity(1, 0, 0, 0.0)

    def shutdown(self, timeout: float = 5.0) -> None:
        self.stopped = timeout


@pytest.fixture()
def manager() -> Iterator[DeferManager]:
    """Return a manager of its own, resetting its pool when the test ends."""
    held = DeferManager()
    yield held
    held.reset()


class TestDeferManager:
    """``DeferManager`` builds both backends once, lazily, from the settings."""

    def test_the_backends_are_built_once_without_a_thread(
        self, manager: DeferManager
    ) -> None:
        before = set(threading.enumerate())
        executor = manager.executor()
        assert isinstance(executor, LaneExecutor)
        assert isinstance(manager.admission(), QueueDelay)
        assert manager.executor() is executor
        assert set(threading.enumerate()) <= before

    def test_capacity_reads_only_a_built_pool(self, manager: DeferManager) -> None:
        assert manager.capacity() is None
        manager.executor()
        capacity = manager.capacity()
        assert capacity is not None
        assert capacity.queued == 0

    @pytest.mark.parametrize(
        ("delay", "expected"), [(0.0, None), (0.25, None), (2.5, 2.5)]
    )
    def test_the_backlog_is_the_wait_past_the_queue_timeout(
        self, manager: DeferManager, delay: float, expected: float | None
    ) -> None:
        lag = Capacity(4, 4, 9, delay)
        with (
            override_next_settings(DEFER={"EXECUTOR": {"QUEUE_TIMEOUT": 0.25}}),
            mock.patch.object(DeferManager, "capacity", return_value=lag),
        ):
            assert manager.backlog() == expected

    def test_an_unbuilt_pool_has_no_backlog(self, manager: DeferManager) -> None:
        assert manager.backlog() is None

    def test_the_settings_size_the_pool_and_name_the_admission(
        self, manager: DeferManager
    ) -> None:
        defer = {
            "EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 3}},
            "ADMISSION": {"BACKEND": f"{__name__}.AlwaysRequest"},
        }
        with override_next_settings(DEFER=defer):
            executor = manager.executor()
            admission = manager.admission()
        assert type(executor) is LaneExecutor
        assert (executor.max_workers, executor.max_queue) == (3, 64)
        assert type(admission) is AlwaysRequest

    @pytest.mark.parametrize(
        ("defer", "read", "error"),
        [
            pytest.param(
                {"DELIVERY": {"BACKEND": "nowhere.Policy"}},
                DeferManager.policy,
                BackendImportError,
                id="policy",
            ),
            pytest.param(
                {"ADMISSION": {"BACKEND": "nowhere.Admission"}},
                DeferManager.admission,
                BackendImportError,
                id="admission",
            ),
            pytest.param(
                {"EXECUTOR": {"BACKEND": "nowhere.Executor"}},
                DeferManager.executor,
                BackendImportError,
                id="executor",
            ),
        ],
    )
    def test_a_backend_that_does_not_import_raises(
        self,
        manager: DeferManager,
        defer: dict[str, object],
        read: Callable[[DeferManager], object],
        error: type[Exception],
    ) -> None:
        with override_next_settings(DEFER=defer), pytest.raises(error, match="nowhere"):
            read(manager)

    def test_the_policy_reads_its_options(self, manager: DeferManager) -> None:
        policy = {
            "BACKEND": "next.defer.DefaultDeliveryPolicy",
            "OPTIONS": {"STREAM": False},
        }
        with override_next_settings(DEFER={"DELIVERY": policy}):
            built = manager.policy()
        assert built.options["STREAM"] is False
        assert manager.policy() is built

    def test_misshaped_entries_raise(self, manager: DeferManager) -> None:
        defer = {"EXECUTOR": "not a mapping", "ADMISSION": 3}
        with override_next_settings(DEFER=defer):
            for build in (manager.executor, manager.admission):
                with pytest.raises(ImproperlyConfigured, match=r"EXECUTOR.*ADMISSION"):
                    build()

    def test_a_backend_outside_its_family_raises(self, manager: DeferManager) -> None:
        with (
            override_next_settings(DEFER={"ADMISSION": {"BACKEND": "builtins.object"}}),
            pytest.raises(ImproperlyConfigured, match="subclass"),
        ):
            manager.admission()

    def test_forget_drops_every_holder_and_spares_the_executor(
        self, manager: DeferManager
    ) -> None:
        held = (manager.admission(), manager.policy(), manager.tokens())
        manager.forget()
        assert manager.capacity() is None
        assert manager.admission() is not held[0]
        assert manager.policy() is not held[1]
        assert manager.tokens() is not held[2]

    def test_reset_stops_the_executor_and_tolerates_an_empty_manager(
        self, manager: DeferManager
    ) -> None:
        manager.reset()
        executor = manager.executor()
        manager.reset()
        with pytest.raises(ExecutorStoppedError):
            executor.submit(StealableTask(lambda: 1))
        assert manager.executor() is not executor

    def test_forget_drops_the_backends_without_stopping_them(
        self, manager: DeferManager
    ) -> None:
        executor = manager.executor()
        manager.forget()
        try:
            assert manager.executor() is not executor
            assert executor.submit(StealableTask(lambda: 4)).result(WAIT) == 4
        finally:
            executor.shutdown(5)

    def test_the_reload_receiver_resets_the_process_manager(self) -> None:
        first = defer_manager.executor()
        try:
            forget_defer_backends(sender=object)
            with pytest.raises(ExecutorStoppedError):
                first.submit(StealableTask(lambda: 1))
            assert defer_manager.executor() is not first
        finally:
            defer_manager.reset()

    def test_a_settings_change_rebuilds_the_process_backends(self) -> None:
        first = defer_manager.executor()
        try:
            executor = {"OPTIONS": {"MAX_WORKERS": 3}}
            with override_next_settings(DEFER={"EXECUTOR": executor}):
                second = defer_manager.executor()
                assert second is not first
                assert isinstance(second, LaneExecutor)
                assert second.max_workers == 3
            with pytest.raises(ExecutorStoppedError):
                first.submit(StealableTask(lambda: 1))
            assert defer_manager.executor() is not second
        finally:
            defer_manager.reset()


class TestExecutorBackend:
    """``DEFER["EXECUTOR"]["BACKEND"]`` names the executor, its ``OPTIONS`` kept."""

    def test_the_settings_name_the_executor(self, manager: DeferManager) -> None:
        executor = {"BACKEND": f"{__name__}.InlineExecutor", "OPTIONS": {"LANES": 2}}
        with override_next_settings(DEFER={"EXECUTOR": executor}):
            built = manager.executor()
            assert isinstance(built, InlineExecutor)
            assert built.options["LANES"] == 2
            assert built.submit(StealableTask(lambda: 7)).result(0) == 7
            assert manager.capacity() == Capacity(1, 0, 0, 0.0)
            manager.reset()
        assert built.stopped == 5.0

    def test_a_class_outside_the_family_is_refused(self, manager: DeferManager) -> None:
        with (
            override_next_settings(DEFER={"EXECUTOR": {"BACKEND": "builtins.object"}}),
            pytest.raises(ImproperlyConfigured, match="subclass"),
        ):
            manager.executor()


class TestBackendLoaded:
    """``defer_backend_loaded`` announces each backend a holder builds."""

    def test_every_holder_announces_its_backend_with_its_entry(
        self, manager: DeferManager
    ) -> None:
        with capture_signals(defer_backend_loaded) as recorder:
            built = (
                manager.executor(),
                manager.admission(),
                manager.policy(),
                manager.tokens(),
                manager.probe(),
            )
            manager.executor()
        senders = [event.sender for event in recorder.events]
        assert senders == [
            LaneExecutor,
            QueueDelay,
            DefaultDeliveryPolicy,
            StackedTokenBackend,
            DefaultServerProbe,
        ]
        assert [event.kwargs["instance"] for event in recorder.events] == list(built)
        config = recorder.events[0].kwargs["config"]
        assert config["BACKEND"] == "next.defer.LaneExecutor"
        assert config["OPTIONS"]["MAX_WORKERS"] == built[0].capacity().workers


class TestTokenBackend:
    """``DeferManager.tokens`` builds the token backend once, lazily."""

    def test_the_shipped_stack_is_built_once(self, manager: DeferManager) -> None:
        backend = manager.tokens()
        assert isinstance(backend, StackedTokenBackend)
        assert manager.tokens() is backend

    def test_a_reset_drops_the_backend(self, manager: DeferManager) -> None:
        backend = manager.tokens()
        manager.reset()
        assert manager.tokens() is not backend

    def test_the_settings_name_the_backend_options(self, manager: DeferManager) -> None:
        tokens = {"OPTIONS": {"CARRIER": "query"}}
        with override_next_settings(DEFER={"TOKENS": tokens}):
            assert isinstance(manager.tokens().carrier, QueryCarrier)

    def test_a_backend_that_does_not_import_is_refused(
        self, manager: DeferManager
    ) -> None:
        with (
            override_next_settings(DEFER={"TOKENS": {"BACKEND": "nowhere.Tokens"}}),
            pytest.raises(BackendImportError),
        ):
            manager.tokens()
