"""The seams that replay thread-local and per-connection state on a pool thread."""

import functools
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Final, Literal

from django.core.exceptions import ImproperlyConfigured
from django.db.backends.base.base import BaseDatabaseWrapper
from django.http import HttpRequest

from next.backends import BackendRoot, resolve_backend_class
from next.conf.defer import backend_paths
from next.errors import BackendNotSubclassError


_SETTING: Final = "NEXT_FRAMEWORK['DEFER'][{key!r}]"


class ThreadState(ABC):
    """Replay `threading.local` state a pool thread does not inherit from the view.

    `covers` names the detected sources the backend replays, by kind or in full.
    """

    covers: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def capture(self, request: HttpRequest) -> object | None:
        """Return the thread-local state to replay, read on the request thread."""

    @abstractmethod
    def apply(self, state: object) -> object:
        """Install the state on the pool thread and return a token for reset."""

    @abstractmethod
    def reset(self, token: object) -> None:
        """Restore the pool thread to its state before apply."""


class ConnectionState(ABC):
    """Replay per-connection state on the connection a pool thread opens.

    `covers` names the detected sources the backend replays, by kind or in full.
    """

    covers: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def capture(self, request: HttpRequest, alias: str) -> object | None:
        """Return the state to replay for this alias, read on the request thread."""

    @abstractmethod
    def apply(self, state: object, connection: BaseDatabaseWrapper) -> None:
        """Replay the captured state with transaction-local statements."""


@dataclass(frozen=True, slots=True)
class Seams:
    """The configured backends of both kinds, in settings order."""

    threads: tuple[ThreadState, ...] = ()
    connections: tuple[ConnectionState, ...] = ()

    @property
    def covers(self) -> frozenset[str]:
        """Return every source any backend of either kind declares it replays."""
        threads = (backend.covers for backend in self.threads)
        connections = (backend.covers for backend in self.connections)
        return frozenset[str]().union(*threads, *connections)


type _Key = Literal["THREAD_STATE", "CONNECTION_STATE"]


def _backends[T](key: _Key, base: BackendRoot[T]) -> tuple[T, ...]:
    """Instantiate every backend `key` lists, refusing a path that names no subclass."""
    setting = _SETTING.format(key=key)
    loaded: list[T] = []
    for path in backend_paths(key):
        try:
            klass = resolve_backend_class({"BACKEND": path}, base=base)
        except ImportError as exc:
            msg = f"{setting} {path!r} could not be imported: {exc}."
            raise ImproperlyConfigured(msg) from exc
        except BackendNotSubclassError as exc:
            msg = f"{setting} {path!r} names no {exc.base_name} subclass."
            raise ImproperlyConfigured(msg) from exc
        loaded.append(klass())
    return tuple(loaded)


@functools.cache
def seams() -> Seams:
    """Return the configured backends, imported once per process."""
    return Seams(
        _backends("THREAD_STATE", ThreadState),
        _backends("CONNECTION_STATE", ConnectionState),
    )


__all__ = ["ConnectionState", "Seams", "ThreadState", "seams"]
