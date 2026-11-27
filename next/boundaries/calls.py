"""The deferred component call a tag hands over, its site and its stable key."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from django.utils.crypto import salted_hmac


if TYPE_CHECKING:
    from django.template import Context

    from .failures import BoundaryFailure, ErrorKind
    from .options import BoundaryOptions, DeferMode


DEFERRALS_KEY: Final = "_next_deferrals"
"""Context key of the `DeferralSink` a page view hands its deferred calls to."""


class Addressing(enum.StrEnum):
    """Whether a boundary is a page body or a component call."""

    PAGE = "page"
    COMPONENT = "component"


@dataclass(frozen=True, slots=True)
class BoundarySite:
    """One component call site in one template, or one page boundary file.

    `name` is the component name or the page path, `origin` the template it sits in.
    """

    addressing: Addressing
    name: str
    mode: DeferMode
    origin: str
    index: int = 0


class CallRenderer(Protocol):
    """How the component tag renders the parts of one deferred call."""

    def loading(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the loading UI, with the error a misfit shape at its site settled."""

    def body(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        """Render the component inside its error boundary, with the error it showed."""

    def error(self, context: Context, kind: ErrorKind) -> tuple[str, BoundaryFailure]:
        """Render the error UI of `kind` without the component, with the error shown."""


@dataclass(frozen=True, slots=True)
class DeferredCall:
    """One deferred component call as its tag hands it over.

    `tag` is the wrapper element, `None` where the boundary adopts the root instead.
    """

    site: BoundarySite
    options: BoundaryOptions
    key: str
    tag: str | None
    source: str | None
    renderer: CallRenderer


_KEY_SALT: Final = "next.defer.key"
_KEY_LENGTH: Final = 8


def stable_key(origin: str, index: int | str) -> str:
    """Return the key of a call site, the same across responses, users and hosts."""
    digest = salted_hmac(_KEY_SALT, f"{origin}:{index}", algorithm="sha256")
    return digest.hexdigest()[:_KEY_LENGTH]


__all__ = [
    "DEFERRALS_KEY",
    "Addressing",
    "BoundarySite",
    "CallRenderer",
    "DeferredCall",
    "stable_key",
]
