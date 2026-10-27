import asyncio
import threading
from unittest.mock import create_autospec

import pytest
from asgiref.sync import sync_to_async
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.realtime import MemoryBroker
from next.realtime.manager import BrokerManager, broker_manager, forget_realtime_broker
from next.realtime.signals import broker_backend_loaded
from next.testing import SignalRecorder


def test_manager_repeated_get_builds_once_and_signals_once() -> None:
    config = {
        "REALTIME": {
            "BROKER": {
                "BACKEND": "next.realtime.MemoryBroker",
                "OPTIONS": {"QUEUE_SIZE": 7},
            }
        }
    }
    with (
        override_settings(NEXT_FRAMEWORK=config),
        SignalRecorder(broker_backend_loaded) as recorder,
    ):
        broker_manager.reset()
        first = broker_manager.get()
        second = broker_manager.get()
    assert first is second
    assert isinstance(first, MemoryBroker)
    assert first._queue_size == 7
    assert len(recorder.events_for(broker_backend_loaded)) == 1


def test_manager_reset_or_forget_rebuilds_a_new_backend() -> None:
    manager = BrokerManager()
    first = manager.get()
    manager.reset()
    assert manager.get() is not first
    cached = broker_manager.get()
    forget_realtime_broker()
    rebuilt = broker_manager.get()
    assert isinstance(rebuilt, MemoryBroker)
    assert rebuilt is not cached


def test_manager_sync_receiver_reentering_get_returns_same_backend() -> None:
    manager = BrokerManager()
    loaded = []

    def receiver(sender, **kwargs):
        loaded.append(manager.get())

    broker_backend_loaded.connect(receiver, sender=MemoryBroker)
    try:
        backend = manager.get()
    finally:
        broker_backend_loaded.disconnect(receiver, sender=MemoryBroker)
    assert loaded == [backend]


def test_manager_aget_awaits_async_receiver() -> None:
    manager = BrokerManager()
    loaded = []

    async def receiver(sender, **kwargs):
        await asyncio.sleep(0)
        loaded.append(kwargs["instance"])

    broker_backend_loaded.connect(receiver, sender=MemoryBroker)
    try:
        backend = asyncio.run(manager.aget())
    finally:
        broker_backend_loaded.disconnect(receiver, sender=MemoryBroker)
    assert loaded == [backend]


def test_manager_non_mapping_broker_config_raises() -> None:
    with (
        override_settings(NEXT_FRAMEWORK={"REALTIME": {"BROKER": "invalid"}}),
        pytest.raises(ImproperlyConfigured, match="must be a mapping"),
    ):
        broker_manager.get()


def test_manager_unimportable_backend_path_raises_improperly_configured() -> None:
    config = {"REALTIME": {"BROKER": {"BACKEND": "missing.realtime.Backend"}}}
    with (
        override_settings(NEXT_FRAMEWORK=config),
        pytest.raises(ImproperlyConfigured, match="Could not import"),
    ):
        broker_manager.get()


def _run_with_deadline(target, seconds: float = 5.0) -> None:
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(seconds)
    assert not worker.is_alive(), "broker manager deadlocked"


def test_manager_receiver_get_from_another_thread_does_not_deadlock() -> None:
    manager = BrokerManager()
    seen = []

    def receiver(sender, **kwargs):
        _run_with_deadline(lambda: seen.append(manager.get()), seconds=2.0)

    broker_backend_loaded.connect(receiver, sender=MemoryBroker)
    try:
        result = []
        _run_with_deadline(lambda: result.append(manager.get()))
    finally:
        broker_backend_loaded.disconnect(receiver, sender=MemoryBroker)
    assert len(result) == 1
    assert seen == result


def test_manager_async_receiver_reentering_manager_does_not_deadlock() -> None:
    manager = BrokerManager()
    seen = []

    def bounded_get() -> None:
        _run_with_deadline(lambda: seen.append(manager.get()), seconds=2.0)

    async def receiver(sender, **kwargs):
        seen.append(await manager.aget())
        await sync_to_async(bounded_get, thread_sensitive=False)()

    broker_backend_loaded.connect(receiver, sender=MemoryBroker)
    try:
        backend = asyncio.run(manager.aget())
    finally:
        broker_backend_loaded.disconnect(receiver, sender=MemoryBroker)
    assert seen == [backend, backend]


def test_manager_concurrent_first_access_signals_exactly_once() -> None:
    manager = BrokerManager()
    barrier = threading.Barrier(8)
    backends = []

    def access() -> None:
        barrier.wait()
        backends.append(manager.get())

    with SignalRecorder(broker_backend_loaded) as recorder:
        workers = [threading.Thread(target=access, daemon=True) for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
    assert len({id(backend) for backend in backends}) == 1
    assert len(backends) == 8
    assert len(recorder.events_for(broker_backend_loaded)) == 1


def test_manager_reset_closes_dropped_backend_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BrokerManager()
    backend = manager.get()
    closed = []
    monkeypatch.setattr(
        backend,
        "close",
        create_autospec(backend.close, side_effect=lambda: closed.append(backend)),
    )
    manager.reset()
    manager.reset()
    assert closed == [backend]


class _RacingLock:
    def __init__(self, manager: BrokerManager, backend: MemoryBroker) -> None:
        self.manager = manager
        self.backend = backend

    def __enter__(self) -> None:
        self.manager._backend = self.backend

    def __exit__(self, *exc_info: object) -> None:
        return None


def test_manager_get_racing_builder_returns_its_backend_without_signal() -> None:
    manager = BrokerManager()
    backend = MemoryBroker()
    manager._lock = _RacingLock(manager, backend)
    with SignalRecorder(broker_backend_loaded) as recorder:
        assert manager.get() is backend
    assert recorder.events_for(broker_backend_loaded) == []


def test_manager_aclose_releases_the_cached_backend_once_and_rebuilds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = BrokerManager()
    backend = manager.get()
    closed: list[object] = []

    async def aclose() -> None:
        closed.append(backend)

    monkeypatch.setattr(backend, "aclose", aclose)
    asyncio.run(manager.aclose())
    asyncio.run(manager.aclose())
    assert closed == [backend]
    assert manager.get() is not backend


def test_manager_aclose_without_a_backend_builds_nothing() -> None:
    manager = BrokerManager()
    with SignalRecorder(broker_backend_loaded) as recorder:
        asyncio.run(manager.aclose())
    assert manager._backend is None
    assert recorder.events_for(broker_backend_loaded) == []
