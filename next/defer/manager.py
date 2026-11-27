"""The façade over the backends of deferred execution, each built on first use."""

from functools import partial
from typing import TYPE_CHECKING

from next.backends import BackendRoot, SingleBackendManager
from next.caches import FORK, FRAMEWORK
from next.conf.defer import SCOPE, defer_option, defer_seconds

from .admission import Admission
from .executors import BoundaryExecutor
from .policy import DeliveryPolicy
from .probe import ServerProbe, server_probes
from .signals import defer_backend_loaded
from .tokens import BoundaryTokenBackend


if TYPE_CHECKING:
    from .markers import Capacity


def _entry[T](key: str, base: BackendRoot[T]) -> SingleBackendManager[T]:
    """Return the holder of the backend the `DEFER` entry `key` names."""
    return SingleBackendManager(
        SCOPE, base=base, signal=defer_backend_loaded, config=partial(defer_option, key)
    )


class DeferManager:
    """Hold the pool executor, the admission, the policy, the probe and the tokens.

    Each holder builds under its own double-checked lock, and `forget` swaps the locks.
    """

    def __init__(self) -> None:
        """Hold no backend until the first use."""
        self._executor = _entry("EXECUTOR", BoundaryExecutor)
        self._admission = _entry("ADMISSION", Admission)
        self._policy = _entry("DELIVERY", DeliveryPolicy)
        self._tokens = _entry("TOKENS", BoundaryTokenBackend)

    def forget(self) -> None:
        """Drop the backends without stopping them, as a forked child must."""
        self._executor.forget()
        self._admission.forget()
        self._policy.forget()
        self._tokens.forget()
        server_probes.forget()

    def executor(self) -> BoundaryExecutor:
        """Return the pool executor, building it on the first call."""
        return self._executor.get()

    def admission(self) -> Admission:
        """Return the admission, building it on the first call."""
        return self._admission.get()

    def capacity(self) -> "Capacity | None":
        """Return the pool lane reading, `None` while no job has built the executor."""
        held = self._executor.peek()
        return None if held is None else held.capacity()

    def backlog(self) -> float | None:
        """Return the wait of the oldest queued job, `None` while within its timeout.

        No job built the pool yet in a process that never queued one, so none waits.
        """
        capacity = self.capacity()
        if capacity is None:
            return None
        waited = capacity.delay
        if waited <= defer_seconds("EXECUTOR", "QUEUE_TIMEOUT"):
            return None
        return waited

    def policy(self) -> DeliveryPolicy:
        """Return the delivery policy, building it on the first call."""
        return self._policy.get()

    def probe(self) -> ServerProbe:
        """Return the server probe, building it on the first call."""
        return server_probes.get()

    def tokens(self) -> BoundaryTokenBackend:
        """Return the boundary token backend, building it on the first call."""
        return self._tokens.get()

    def reset(self) -> None:
        """Stop the executor and drop every backend, so a next use rereads settings."""
        executor = self._executor.reset()
        self._admission.reset()
        self._policy.reset()
        self._tokens.reset()
        server_probes.reset()
        if executor is not None:
            executor.shutdown()


defer_manager = DeferManager()
FORK.attach(defer_manager.forget)


def forget_defer_backends(**kwargs: object) -> None:
    """Reset the manager when the framework settings reload."""
    defer_manager.reset()


FRAMEWORK.attach(forget_defer_backends)


__all__ = ["DeferManager", "defer_manager", "forget_defer_backends"]
