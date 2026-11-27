"""Whether a pool-placed job queues, runs on its request thread or is shed."""

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import override

from next.conf.defer import defer_count, defer_seconds, entry_options

from .markers import Admit, Capacity


class Admission(ABC):
    """Decide between the pool queue, the request thread and shedding, per reading."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Keep the `OPTIONS` of the settings entry."""
        self.options: Mapping[str, object] = entry_options(config)

    @abstractmethod
    def admit(self, capacity: Capacity) -> Admit:
        """Return POOL, REQUEST or SHED for a job the pool in `capacity` would take.

        The verdict reads only `capacity`, so one reading serves every unit it admits.
        """


class QueueDelay(Admission):
    """Queue a job, hand it to its request thread once the pool lags, or shed it.

    Past `SHED_DELAY` the process is behind, so a job settles as `overloaded` at once.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Read the delay targets and the queue bound of the `EXECUTOR` settings."""
        super().__init__(config)
        self.timeout = defer_seconds("EXECUTOR", "QUEUE_TIMEOUT")
        self.shed = defer_seconds("EXECUTOR", "SHED_DELAY")
        self.limit = defer_count("EXECUTOR", "OPTIONS", "MAX_QUEUE")

    @override
    def admit(self, capacity: Capacity) -> Admit:
        """Return SHED past `SHED_DELAY`, REQUEST on a lag or full queue, else POOL."""
        if capacity.delay >= self.shed:
            return Admit.SHED
        if capacity.delay > self.timeout or capacity.queued >= self.limit:
            return Admit.REQUEST
        return Admit.POOL


__all__ = ["Admission", "QueueDelay"]
