from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured

from next.static import StaticBackend, StaticFilesBackend, default_kinds


class FakeBackend:
    """Base of the fake backend family the loader builds in these tests."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Keep the settings entry the backend was built from."""
        self.config = config


class AlphaBackend(FakeBackend):
    """A valid backend of the family."""


class BetaBackend(FakeBackend):
    """A second valid backend, used to assert ordering and rebuilds."""


class ForeignBackend:
    """A backend outside the family, so the base check rejects it."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Accept the settings entry like a real backend would."""
        self.config = config


class RaisingBackend(FakeBackend):
    """A backend whose construction fails with the error its entry names."""

    ERRORS: ClassVar[dict[str, type[Exception]]] = {
        "config": ImproperlyConfigured,
        "import": ImportError,
        "type": TypeError,
        "value": ValueError,
    }

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Raise the error class named under `ERROR`."""
        msg = "boom"
        raise self.ERRORS[str(config["ERROR"])](msg)


class CountingBackend(FakeBackend):
    """A backend counting how many times the family instantiated it."""

    instances: ClassVar[int] = 0

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Record one more instantiation."""
        super().__init__(config)
        CountingBackend.instances += 1


class AbstractFakeBackend(ABC):
    """An abstract family root, like the contracts the real areas declare."""

    def __init__(self, config: Mapping[str, Any]) -> None:
        """Keep the settings entry the backend was built from."""
        self.config = config

    @abstractmethod
    def run(self) -> str:
        """Return whatever the family asks its members for."""


class ConcreteFakeBackend(AbstractFakeBackend):
    """The instantiable member of the abstract family."""

    def run(self) -> str:
        """Answer for the family."""
        return "ok"


def not_a_class(config: Mapping[str, Any]) -> FakeBackend:
    """Return a backend, so only the class check can reject this dotted path."""
    return AlphaBackend(config)


ALPHA = f"{__name__}.AlphaBackend"
BETA = f"{__name__}.BetaBackend"
FOREIGN = f"{__name__}.ForeignBackend"
RAISING = f"{__name__}.RaisingBackend"
COUNTING = f"{__name__}.CountingBackend"
CONCRETE = f"{__name__}.ConcreteFakeBackend"
NOT_A_CLASS = f"{__name__}.not_a_class"
MISSING = f"{__name__}.NoSuchBackend"


FILE_COMPONENTS_BACKEND = "next.components.FileComponentsBackend"


def file_components_entry(*dirs: Path) -> dict[str, Any]:
    """Build one ``COMPONENT_BACKENDS`` entry listing the given directories."""
    return {
        "BACKEND": FILE_COMPONENTS_BACKEND,
        "DIRS": [str(p) for p in dirs],
        "COMPONENTS_DIR": "_components",
    }


class MockAutoreloadSender:
    """Minimal ``autoreload_started`` sender that records what it was asked to watch."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.watch_calls: list[tuple[object, str]] = []

    def watch_dir(self, path: object, glob: str) -> None:
        """Record the directory and glob the framework asked to watch."""
        self.watch_calls.append((path, glob))


class PlainStaticBackend(StaticFilesBackend):
    """Static backend with deterministic URLs and no bookkeeping of its own.

    The counterpart of `RecordingStaticBackend` for a benchmark, where a list
    growing inside the timed loop would be measured along with the framework.
    """

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Return the URL the logical name and kind spell."""
        del source_path
        return f"/static/next/{logical_name}{default_kinds.extension(kind)}"


class RecordingStaticBackend(StaticFilesBackend):
    """Static backend with deterministic URLs that logs what it registered.

    The URLs keep discovery-order assertions readable, and the log is what
    tells a warm render that registered nothing from a cold one that did.
    """

    def __init__(self) -> None:
        """Start with no recorded registration."""
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def register_file(self, source_path: Path, logical_name: str, kind: str) -> str:
        """Record the registration and return the URL the logical name spells."""
        del source_path
        self.calls.append((logical_name, kind))
        return f"/static/next/{logical_name}{default_kinds.extension(kind)}"


class StaticAssetProvider:
    """The narrow ``BackendProvider`` the asset discovery layer reads.

    Holds a backend and already resolved page roots, so a test wires discovery
    up without standing a whole static manager behind it.
    """

    def __init__(self, backend: StaticBackend, roots: tuple[Path, ...] = ()) -> None:
        """Keep the backend and the resolved page trees to report."""
        self._backend = backend
        self._roots = roots

    @property
    def default_backend(self) -> StaticBackend:
        """Return the backend every registration goes through."""
        return self._backend

    def page_roots(self) -> tuple[Path, ...]:
        """Return the resolved page trees discovery walks within."""
        return self._roots
