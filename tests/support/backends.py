from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar

from django.core.exceptions import ImproperlyConfigured


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
