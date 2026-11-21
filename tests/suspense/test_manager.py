import threading

import pytest
from django.core.exceptions import ImproperlyConfigured

from next.errors import BackendImportError, SettingImportError
from next.suspense import (
    Admit,
    Capacity,
    ExecutorStoppedError,
    LaneExecutor,
    QueueDelay,
)
from next.suspense.manager import (
    SuspenseManager,
    forget_suspense_backends,
    suspense_manager,
)
from next.suspense.tokens import QueryTransport, StackedTokenBackend
from next.tasks import StealableTask
from next.testing import override_next_settings


class RecordingPool(LaneExecutor):
    """A pool subclass a settings entry can name."""


class AlwaysRequest(QueueDelay):
    """An admission subclass a settings entry can name."""

    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        return Admit.REQUEST


class TestSuspenseManager:
    """``SuspenseManager`` builds both backends once, lazily, from the settings."""

    def test_the_backends_are_built_once_without_a_thread(self) -> None:
        before = set(threading.enumerate())
        manager = SuspenseManager()
        executor = manager.executor()
        try:
            assert isinstance(executor, LaneExecutor)
            assert isinstance(manager.admission(), QueueDelay)
            assert manager.executor() is executor
            assert set(threading.enumerate()) <= before
        finally:
            manager.reset()

    def test_capacity_reads_only_a_built_pool(self) -> None:
        manager = SuspenseManager()
        assert manager.capacity() is None
        manager.executor()
        try:
            capacity = manager.capacity()
            assert capacity is not None
            assert capacity.queued == 0
        finally:
            manager.reset()

    def test_the_settings_name_the_backends_and_their_options(self) -> None:
        defer = {
            "EXECUTOR": {
                "BACKEND": f"{__name__}.RecordingPool",
                "OPTIONS": {"MAX_WORKERS": 3},
            },
            "ADMISSION": f"{__name__}.AlwaysRequest",
        }
        manager = SuspenseManager()
        with override_next_settings(DEFER=defer):
            executor = manager.executor()
            admission = manager.admission()
        try:
            assert type(executor) is RecordingPool
            assert (executor.max_workers, executor.max_queue) == (3, 64)
            assert type(admission) is AlwaysRequest
        finally:
            manager.reset()

    def test_a_backend_that_does_not_import_raises(self) -> None:
        manager = SuspenseManager()
        with (
            override_next_settings(DEFER={"EXECUTOR": {"BACKEND": "nowhere.Pool"}}),
            pytest.raises(BackendImportError, match="nowhere"),
        ):
            manager.executor()

    def test_a_policy_that_does_not_import_raises(self) -> None:
        manager = SuspenseManager()
        with (
            override_next_settings(DEFER={"POLICY": {"BACKEND": "nowhere.Policy"}}),
            pytest.raises(BackendImportError, match="nowhere"),
        ):
            manager.policy()

    def test_the_policy_reads_its_options(self) -> None:
        manager = SuspenseManager()
        policy = {
            "BACKEND": "next.suspense.DefaultStreamPolicy",
            "OPTIONS": {"STREAM": False},
        }
        with override_next_settings(DEFER={"POLICY": policy}):
            built = manager.policy()
        assert built.options["STREAM"] is False
        assert manager.policy() is built

    def test_an_admission_that_does_not_import_raises(self) -> None:
        manager = SuspenseManager()
        with (
            override_next_settings(DEFER={"ADMISSION": "nowhere.Admission"}),
            pytest.raises(SettingImportError, match="nowhere"),
        ):
            manager.admission()

    def test_misshaped_entries_build_the_defaults(self) -> None:
        manager = SuspenseManager()
        defer = {"EXECUTOR": "not a mapping", "ADMISSION": 3}
        with override_next_settings(DEFER=defer):
            executor = manager.executor()
            admission = manager.admission()
        try:
            assert type(executor) is LaneExecutor
            assert executor.max_workers == 16
            assert type(admission) is QueueDelay
        finally:
            manager.reset()

    @pytest.mark.parametrize(
        "defer",
        [
            {"EXECUTOR": {"BACKEND": "builtins.object"}},
            {"ADMISSION": "builtins.object"},
        ],
    )
    def test_a_backend_outside_its_family_raises(
        self, defer: dict[str, object]
    ) -> None:
        manager = SuspenseManager()
        with (
            override_next_settings(DEFER=defer),
            pytest.raises(ImproperlyConfigured, match="subclass"),
        ):
            manager.admission()

    def test_reset_stops_the_executor_and_tolerates_an_empty_manager(self) -> None:
        manager = SuspenseManager()
        manager.reset()
        executor = manager.executor()
        manager.reset()
        with pytest.raises(ExecutorStoppedError):
            executor.submit(StealableTask(lambda: 1))
        rebuilt = manager.executor()
        try:
            assert rebuilt is not executor
        finally:
            manager.reset()

    def test_forget_drops_the_backends_without_stopping_them(self) -> None:
        manager = SuspenseManager()
        executor = manager.executor()
        manager.forget()
        try:
            assert manager.executor() is not executor
            assert executor.submit(StealableTask(lambda: 4)).result(5) == 4
        finally:
            executor.shutdown(5)
            manager.reset()

    def test_the_reload_receiver_resets_the_process_manager(self) -> None:
        first = suspense_manager.executor()
        try:
            forget_suspense_backends(sender=object)
            with pytest.raises(ExecutorStoppedError):
                first.submit(StealableTask(lambda: 1))
            assert suspense_manager.executor() is not first
        finally:
            suspense_manager.reset()

    def test_a_settings_change_rebuilds_the_process_backends(self) -> None:
        first = suspense_manager.executor()
        try:
            options = {"MAX_WORKERS": 3}
            with override_next_settings(DEFER={"EXECUTOR": {"OPTIONS": options}}):
                second = suspense_manager.executor()
                assert second is not first
                assert isinstance(second, LaneExecutor)
                assert second.max_workers == 3
            with pytest.raises(ExecutorStoppedError):
                first.submit(StealableTask(lambda: 1))
            assert suspense_manager.executor() is not second
        finally:
            suspense_manager.reset()


class TestTokenBackend:
    """``SuspenseManager.tokens`` builds the token backend once, lazily."""

    def test_the_shipped_stack_is_built_once(self) -> None:
        manager = SuspenseManager()
        backend = manager.tokens()
        assert isinstance(backend, StackedTokenBackend)
        assert manager.tokens() is backend

    def test_a_reset_drops_the_backend(self) -> None:
        manager = SuspenseManager()
        backend = manager.tokens()
        manager.reset()
        assert manager.tokens() is not backend

    def test_the_settings_name_the_backend_options(self) -> None:
        manager = SuspenseManager()
        tokens = {"OPTIONS": {"TRANSPORT": "next.suspense.QueryTransport"}}
        with override_next_settings(DEFER={"TOKENS": tokens}):
            assert isinstance(manager.tokens().transport, QueryTransport)

    def test_a_backend_that_does_not_import_is_refused(self) -> None:
        manager = SuspenseManager()
        with (
            override_next_settings(DEFER={"TOKENS": {"BACKEND": "nowhere.Tokens"}}),
            pytest.raises(BackendImportError),
        ):
            manager.tokens()
