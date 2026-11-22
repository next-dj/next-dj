"""Whether a pool-placed job queues, runs on its request thread or is shed."""

from abc import ABC, abstractmethod
from typing import override

from next.conf.defer import defer_count, defer_seconds
from next.suspense.markers import Admit, Capacity


class Admission(ABC):
    """Decide between the pool queue, the request thread and shedding, per reading."""

    @abstractmethod
    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        """Return POOL, REQUEST or SHED for a job the pool in `capacity` would take.

        The verdict reads only `capacity` and `forced`, one reading serving every unit.
        """


class QueueDelay(Admission):
    """Queue a job unless the pool lags past `QUEUE_TIMEOUT` or its queue is full."""

    def __init__(self) -> None:
        """Read the queue delay target and the queue bound of the `EXECUTOR` options."""
        self.timeout = defer_seconds("EXECUTOR", "OPTIONS", "QUEUE_TIMEOUT")
        self.limit = defer_count("EXECUTOR", "OPTIONS", "MAX_QUEUE")

    @override
    def admit(self, capacity: Capacity, *, forced: bool) -> Admit:
        """Return REQUEST for a forced job, a lagging or a full queue, else POOL."""
        if forced or capacity.delay > self.timeout or capacity.queued >= self.limit:
            return Admit.REQUEST
        return Admit.POOL


__all__ = ["Admission", "QueueDelay"]
