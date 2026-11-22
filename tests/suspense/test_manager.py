import threading
from collections.abc import Callable, Iterator

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


@pytest.fixture()
def manager() -> Iterator[SuspenseManager]:
    """Return a manager of its own, resetting its pool when the test ends."""
    held = SuspenseManager()
    yield held
    held.reset()


class TestSuspenseManager:
    """``SuspenseManager`` builds both backends once, lazily, from the settings."""

    def test_the_backends_are_built_once_without_a_thread(
        self, manager: SuspenseManager
    ) -> None:
        before = set(threading.enumerate())
        executor = manager.executor()
        assert isinstance(executor, LaneExecutor)
        assert isinstance(manager.admission(), QueueDelay)
        assert manager.executor() is executor
        assert set(threading.enumerate()) <= before

    def test_capacity_reads_only_a_built_pool(self, manager: SuspenseManager) -> None:
        assert manager.capacity() is None
        manager.executor()
        capacity = manager.capacity()
        assert capacity is not None
        assert capacity.queued == 0

    def test_the_settings_name_the_backends_and_their_options(
        self, manager: SuspenseManager
    ) -> None:
        defer = {
            "EXECUTOR": {
                "BACKEND": f"{__name__}.RecordingPool",
                "OPTIONS": {"MAX_WORKERS": 3},
            },
            "ADMISSION": f"{__name__}.AlwaysRequest",
        }
        with override_next_settings(DEFER=defer):
            executor = manager.executor()
            admission = manager.admission()
        assert type(executor) is RecordingPool
        assert (executor.max_workers, executor.max_queue) == (3, 64)
        assert type(admission) is AlwaysRequest

    @pytest.mark.parametrize(
        ("defer", "read", "error"),
        [
            pytest.param(
                {"EXECUTOR": {"BACKEND": "nowhere.Pool"}},
                SuspenseManager.executor,
                BackendImportError,
                id="executor",
            ),
            pytest.param(
                {"POLICY": {"BACKEND": "nowhere.Policy"}},
                SuspenseManager.policy,
                BackendImportError,
                id="policy",
            ),
            pytest.param(
                {"ADMISSION": "nowhere.Admission"},
                SuspenseManager.admission,
                SettingImportError,
                id="admission",
            ),
        ],
    )
    def test_a_backend_that_does_not_import_raises(
        self,
        manager: SuspenseManager,
        defer: dict[str, object],
        read: Callable[[SuspenseManager], object],
        error: type[Exception],
    ) -> None:
        with override_next_settings(DEFER=defer), pytest.raises(error, match="nowhere"):
            read(manager)

    def test_the_policy_reads_its_options(self, manager: SuspenseManager) -> None:
        policy = {
            "BACKEND": "next.suspense.DefaultStreamPolicy",
            "OPTIONS": {"STREAM": False},
        }
        with override_next_settings(DEFER={"POLICY": policy}):
            built = manager.policy()
        assert built.options["STREAM"] is False
        assert manager.policy() is built

    def test_misshaped_entries_build_the_defaults(
        self, manager: SuspenseManager
    ) -> None:
        defer = {"EXECUTOR": "not a mapping", "ADMISSION": 3}
        with override_next_settings(DEFER=defer):
            executor = manager.executor()
            admission = manager.admission()
        assert type(executor) is LaneExecutor
        assert executor.max_workers == 16
        assert type(admission) is QueueDelay

    @pytest.mark.parametrize(
        ("defer", "read"),
        [
            ({"EXECUTOR": {"BACKEND": "builtins.object"}}, SuspenseManager.executor),
            ({"ADMISSION": "builtins.object"}, SuspenseManager.admission),
        ],
        ids=["executor", "admission"],
    )
    def test_a_backend_outside_its_family_raises(
        self,
        manager: SuspenseManager,
        defer: dict[str, object],
        read: Callable[[SuspenseManager], object],
    ) -> None:
        with (
            override_next_settings(DEFER=defer),
            pytest.raises(ImproperlyConfigured, match="subclass"),
        ):
            read(manager)

    def test_forget_drops_every_holder_and_spares_the_executor(
        self, manager: SuspenseManager
    ) -> None:
        held = (manager.admission(), manager.policy(), manager.tokens())
        manager.forget()
        assert manager.capacity() is None
        assert manager.admission() is not held[0]
        assert manager.policy() is not held[1]
        assert manager.tokens() is not held[2]

    def test_reset_stops_the_executor_and_tolerates_an_empty_manager(
        self, manager: SuspenseManager
    ) -> None:
        manager.reset()
        executor = manager.executor()
        manager.reset()
        with pytest.raises(ExecutorStoppedError):
            executor.submit(StealableTask(lambda: 1))
        assert manager.executor() is not executor

    def test_forget_drops_the_backends_without_stopping_them(
        self, manager: SuspenseManager
    ) -> None:
        executor = manager.executor()
        manager.forget()
        try:
            assert manager.executor() is not executor
            assert executor.submit(StealableTask(lambda: 4)).result(5) == 4
        finally:
            executor.shutdown(5)

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

    def test_the_shipped_stack_is_built_once(self, manager: SuspenseManager) -> None:
        backend = manager.tokens()
        assert isinstance(backend, StackedTokenBackend)
        assert manager.tokens() is backend

    def test_a_reset_drops_the_backend(self, manager: SuspenseManager) -> None:
        backend = manager.tokens()
        manager.reset()
        assert manager.tokens() is not backend

    def test_the_settings_name_the_backend_options(
        self, manager: SuspenseManager
    ) -> None:
        tokens = {"OPTIONS": {"TRANSPORT": "next.suspense.QueryTransport"}}
        with override_next_settings(DEFER={"TOKENS": tokens}):
            assert isinstance(manager.tokens().transport, QueryTransport)

    def test_a_backend_that_does_not_import_is_refused(
        self, manager: SuspenseManager
    ) -> None:
        with (
            override_next_settings(DEFER={"TOKENS": {"BACKEND": "nowhere.Tokens"}}),
            pytest.raises(BackendImportError),
        ):
            manager.tokens()
