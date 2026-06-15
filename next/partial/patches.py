"""Patch envelope value objects, the minimal builder, and PatchResponse."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from django.http import HttpResponse

from .headers import (
    CONTENT_TYPE,
    RESPONSE_VERSION,
    set_partial_vary,
)


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


HTML_VERBS: frozenset[str] = frozenset({"replace", "inner"})
TARGET_KEYS: frozenset[str] = frozenset({"zone", "form", "field", "css"})


@dataclass(frozen=True, slots=True)
class Patch:
    """One addressed DOM operation of a patch envelope.

    A patch carries a verb, an optional target object with a single key,
    optional HTML payload, and any verb-specific extras.
    """

    op: str
    target: "Mapping[str, Any] | None" = None
    html: str | None = None
    extras: "Mapping[str, Any]" = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the patch as an ordered mapping."""
        data: dict[str, Any] = {"op": self.op}
        if self.target is not None:
            data["target"] = dict(self.target)
        if self.html is not None:
            data["html"] = self.html
        data.update(self.extras)
        return data


@dataclass(frozen=True, slots=True)
class Asset:
    """One co-located asset of a rendered target, by kind and URL."""

    kind: str
    url: str

    def as_dict(self) -> dict[str, str]:
        """Return the wire form of the asset entry."""
        return {"kind": self.kind, "url": self.url}


@dataclass(frozen=True, slots=True)
class DeferZone:
    """One zone the client should fetch next, with a load trigger."""

    zone: str
    trigger: str = "load"

    def as_dict(self) -> dict[str, str]:
        """Return the wire form of the defer entry."""
        return {"zone": self.zone, "trigger": self.trigger}


@dataclass(frozen=True, slots=True)
class FormMeta:
    """Machine-readable state of a form built from its field specs."""

    uid: str
    valid: bool
    errors: "Mapping[str, Sequence[str]]" = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the form meta object."""
        return {
            "uid": self.uid,
            "valid": self.valid,
            "errors": {name: list(msgs) for name, msgs in self.errors.items()},
        }


@dataclass(frozen=True, slots=True)
class Envelope:
    """A patch envelope carrying ordered ops and protocol meta.

    Every field but `version` is optional, an absent value is empty on
    the wire. The `csrf` and `request_id` meta are stamped only when set
    so the wire shape stays stable whether or not they travel.
    """

    version: str
    ops: "Sequence[Patch]" = ()
    assets: "Sequence[Asset]" = ()
    defer: "Sequence[DeferZone]" = ()
    form: "FormMeta | None" = None
    csrf: "Mapping[str, Any] | None" = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the envelope as an ordered mapping."""
        data: dict[str, Any] = {
            "version": self.version,
            "ops": [op.as_dict() for op in self.ops],
            "assets": [asset.as_dict() for asset in self.assets],
            "defer": [zone.as_dict() for zone in self.defer],
            "form": self.form.as_dict() if self.form is not None else None,
        }
        if self.csrf is not None:
            data["csrf"] = dict(self.csrf)
        if self.request_id is not None:
            data["request_id"] = self.request_id
        return data


class Patches:
    """Builder of a patch envelope from HTML and HTML-less verbs.

    Verbs take a finished `html` string or no payload at all. Targeting
    that renders a zone or component on the builder's behalf is an
    extension point left open on top of this surface.
    """

    def __init__(self, version: str) -> None:
        """Start an empty builder stamped with the asset version."""
        self._version = version
        self._ops: list[Patch] = []
        self._assets: list[Asset] = []
        self._defer: list[DeferZone] = []
        self._form: FormMeta | None = None

    def replace(self, target: "Mapping[str, Any]", html: str) -> "Patches":
        """Replace the target node wholesale with the given HTML."""
        self._ops.append(Patch(op="replace", target=dict(target), html=html))
        return self

    def inner(self, target: "Mapping[str, Any]", html: str) -> "Patches":
        """Replace only the contents of the target with the given HTML."""
        self._ops.append(Patch(op="inner", target=dict(target), html=html))
        return self

    def remove(self, target: "Mapping[str, Any]") -> "Patches":
        """Remove the target node."""
        self._ops.append(Patch(op="remove", target=dict(target)))
        return self

    def event(self, name: str, detail: "Mapping[str, Any] | None" = None) -> "Patches":
        """Dispatch a CustomEvent on document and the `Next.on` bus."""
        self._ops.append(
            Patch(op="event", extras={"name": name, "detail": dict(detail or {})})
        )
        return self

    def add_asset(self, kind: str, url: str) -> "Patches":
        """Record a co-located asset in the envelope manifest."""
        self._assets.append(Asset(kind=kind, url=url))
        return self

    def defer_zone(self, zone: str, trigger: str = "load") -> "Patches":
        """Mark a zone the client should fetch next."""
        self._defer.append(DeferZone(zone=zone, trigger=trigger))
        return self

    def set_form(self, form: FormMeta) -> "Patches":
        """Attach the machine-readable form meta to the envelope."""
        self._form = form
        return self

    def envelope(self) -> Envelope:
        """Return the assembled envelope value object."""
        return Envelope(
            version=self._version,
            ops=tuple(self._ops),
            assets=tuple(self._assets),
            defer=tuple(self._defer),
            form=self._form,
        )


class PatchResponse(HttpResponse):
    """HTTP response that carries a serialized patch envelope.

    The response is an `HttpResponse` subclass so it passes the handler
    normalisation contract that requires rich return types to subclass
    `HttpResponse`. The body bytes and content type come from the active
    protocol backend, the partial Vary headers are set on construction.
    """

    def __init__(
        self,
        body: bytes,
        *,
        content_type: str = CONTENT_TYPE,
        version: str | None = None,
        status: int = 200,
    ) -> None:
        """Build the response from serialized envelope bytes."""
        super().__init__(content=body, content_type=content_type, status=status)
        if version is not None:
            self[RESPONSE_VERSION] = version
        set_partial_vary(self)


__all__ = [
    "Asset",
    "DeferZone",
    "Envelope",
    "FormMeta",
    "Patch",
    "PatchResponse",
    "Patches",
]
