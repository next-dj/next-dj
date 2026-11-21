"""The façade over the backends of deferred execution, each built on first use."""

import threading
from typing import TYPE_CHECKING, cast

from next.backends import instantiate_backend, resolve_backend_class
from next.conf.defer import SCOPE, defer_option
from next.errors import BackendImportError, SettingImportError
from next.suspense.admission import Admission
from next.suspense.executors import BoundaryExecutor
from next.suspense.policy import StreamPolicy
from next.suspense.tokens import BoundaryTokenBackend
from next.suspense.transport import StreamTransport, transports
from next.utils import after_fork


if TYPE_CHECKING:
    from collections.abc import Mapping

    from next.suspense.markers import Capacity


_ADMISSION = "ADMISSION"


def _build_executor() -> BoundaryExecutor:
    """Build the executor `DEFER["EXECUTOR"]` names, with its merged options."""
    config = cast("Mapping[str, object]", defer_option("EXECUTOR"))
    try:
        klass = resolve_backend_class(config, base=BoundaryExecutor)
    except ImportError as exc:
        raise BackendImportError(SCOPE, exc) from exc
    return instantiate_backend(klass, config)


def _build_policy() -> StreamPolicy:
    """Build the delivery policy `DEFER["POLICY"]` names, with its merged options."""
    config = cast("Mapping[str, object]", defer_option("POLICY"))
    try:
        klass = resolve_backend_class(config, base=StreamPolicy)
    except ImportError as exc:
        raise BackendImportError(SCOPE, exc) from exc
    return instantiate_backend(klass, config)


def _build_tokens() -> BoundaryTokenBackend:
    """Build the token backend `DEFER["TOKENS"]` names, with its merged options."""
    config = cast("Mapping[str, object]", defer_option("TOKENS"))
    try:
        klass = resolve_backend_class(config, base=BoundaryTokenBackend)
    except ImportError as exc:
        raise BackendImportError(SCOPE, exc) from exc
    return instantiate_backend(klass, config)


def _build_admission() -> Admission:
    """Build the admission `DEFER["ADMISSION"]` names, its default for a misfit."""
    dotted = cast("str", defer_option(_ADMISSION))
    try:
        klass = resolve_backend_class({"BACKEND": dotted}, base=Admission)
    except ImportError as exc:
        raise SettingImportError(_ADMISSION, dotted, exc, scope=SCOPE) from exc
    return klass()


class SuspenseManager:
    """Hold the pool executor, the admission, the policy, the transport and the tokens.

    The executor and the admission build together on the first job, the rest alone.
    """

    def __init__(self) -> None:
        """Hold no backend until the first use."""
        self.forget()

    def forget(self) -> None:
        """Drop the backends without stopping them, as a forked child must."""
        self._lock = threading.Lock()
        self._backends: tuple[BoundaryExecutor, Admission] | None = None
        self._policy: StreamPolicy | None = None
        self._tokens: BoundaryTokenBackend | None = None
        transports.forget()

    def executor(self) -> BoundaryExecutor:
        """Return the pool executor, building the backends on the first call."""
        return self._ensure_backends()[0]

    def admission(self) -> Admission:
        """Return the admission, building the backends on the first call."""
        return self._ensure_backends()[1]

    def capacity(self) -> "Capacity | None":
        """Return the pool lane reading, `None` while no job has built the executor."""
        held = self._backends
        return None if held is None else held[0].capacity()

    def policy(self) -> StreamPolicy:
        """Return the delivery policy, building it on the first call."""
        held = self._policy
        if held is None:
            with self._lock:
                held = self._policy or _build_policy()
                self._policy = held
        return held

    def transport(self) -> StreamTransport:
        """Return the transport backend, building it on the first call."""
        return transports.backend()

    def tokens(self) -> BoundaryTokenBackend:
        """Return the boundary token backend, building it on the first call."""
        held = self._tokens
        if held is None:
            with self._lock:
                held = self._tokens or _build_tokens()
                self._tokens = held
        return held

    def _ensure_backends(self) -> tuple[BoundaryExecutor, Admission]:
        """Build both backends once, double-checked under the lock."""
        held = self._backends
        if held is None:
            with self._lock:
                held = self._backends or (_build_executor(), _build_admission())
                self._backends = held
        return held

    def reset(self) -> None:
        """Stop the executor and drop every backend, so a next use rereads settings."""
        with self._lock:
            held, self._backends = self._backends, None
            self._policy = None
            self._tokens = None
        transports.reset()
        if held is not None:
            held[0].shutdown()


suspense_manager = SuspenseManager()
after_fork(suspense_manager.forget)


def forget_suspense_backends(**kwargs: object) -> None:
    """Reset the manager when the framework settings reload."""
    suspense_manager.reset()


__all__ = ["SuspenseManager", "forget_suspense_backends", "suspense_manager"]
