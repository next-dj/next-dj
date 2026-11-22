"""The façade over the backends of deferred execution, each built on first use."""

from functools import partial
from typing import TYPE_CHECKING, Final, override

from next.backends import BackendRoot, SingleBackendManager, resolve_backend_class
from next.conf.defer import SCOPE, defer_option, defer_read
from next.conf.shapes import text
from next.errors import SettingImportError
from next.suspense.backends import Admission
from next.suspense.executors import BoundaryExecutor
from next.suspense.policy import StreamPolicy
from next.suspense.tokens import BoundaryTokenBackend
from next.suspense.transport import StreamTransport, transports
from next.utils import after_fork


if TYPE_CHECKING:
    from next.suspense.markers import Capacity


_ADMISSION: Final = "ADMISSION"


class _AdmissionManager(SingleBackendManager[Admission]):
    """Hold the admission `DEFER["ADMISSION"]` names by a bare path, with no options."""

    @override
    def build(self) -> Admission:
        """Return the admission the path names, its default for a misfit."""
        dotted = defer_read(text, _ADMISSION)
        try:
            klass = resolve_backend_class({"BACKEND": dotted}, base=Admission)
        except ImportError as exc:
            raise SettingImportError(_ADMISSION, dotted, exc, scope=SCOPE) from exc
        return klass()


def _entry[T](key: str, base: BackendRoot[T]) -> SingleBackendManager[T]:
    """Return the holder of the backend the `DEFER` entry `key` names."""
    return SingleBackendManager(SCOPE, base=base, config=partial(defer_option, key))


class SuspenseManager:
    """Hold the pool executor, the admission, the policy, the transport and the tokens.

    Each holder builds under its own double-checked lock, and `forget` swaps the locks.
    """

    def __init__(self) -> None:
        """Hold no backend until the first use."""
        self._executor = _entry("EXECUTOR", BoundaryExecutor)
        self._admission = _AdmissionManager(_ADMISSION, base=Admission)
        self._policy = _entry("POLICY", StreamPolicy)
        self._tokens = _entry("TOKENS", BoundaryTokenBackend)

    def forget(self) -> None:
        """Drop the backends without stopping them, as a forked child must."""
        self._executor.forget()
        self._admission.forget()
        self._policy.forget()
        self._tokens.forget()
        transports.forget()

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

    def policy(self) -> StreamPolicy:
        """Return the delivery policy, building it on the first call."""
        return self._policy.get()

    def transport(self) -> StreamTransport:
        """Return the transport backend, building it on the first call."""
        return transports.get()

    def tokens(self) -> BoundaryTokenBackend:
        """Return the boundary token backend, building it on the first call."""
        return self._tokens.get()

    def reset(self) -> None:
        """Stop the executor and drop every backend, so a next use rereads settings."""
        executor = self._executor.reset()
        self._admission.reset()
        self._policy.reset()
        self._tokens.reset()
        transports.reset()
        if executor is not None:
            executor.shutdown()


suspense_manager = SuspenseManager()
after_fork(suspense_manager.forget)


def forget_suspense_backends(**kwargs: object) -> None:
    """Reset the manager when the framework settings reload."""
    suspense_manager.reset()


__all__ = ["SuspenseManager", "forget_suspense_backends", "suspense_manager"]
