"""Value objects of a patch envelope and their wire forms.

The module stays free of request and rendering machinery so the envelope
shape can be built and serialized outside a Django request cycle.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from . import keys
from .errors import ReservedPatchKeyError


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class Patch:
    """One addressed DOM operation of a patch envelope."""

    op: str
    target: "Mapping[str, Any] | None" = None
    html: str | None = None
    extras: "Mapping[str, Any]" = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Refuse an extras payload that names a structural wire key."""
        collision = keys.RESERVED_PATCH_KEYS & self.extras.keys()
        if collision:
            raise ReservedPatchKeyError(self.op, frozenset(collision))

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the patch as an ordered mapping."""
        data: dict[str, Any] = {keys.OP: self.op}
        if self.target is not None:
            data[keys.TARGET] = dict(self.target)
        if self.html is not None:
            data[keys.HTML] = self.html
        data.update(self.extras)
        return data


@dataclass(frozen=True, slots=True)
class Asset:
    """One co-located asset of a rendered target by kind, URL, and inline body.

    The `load` field is the client insertion verb resolved from the kind
    registry. It stays None for a kind the runtime cannot insert, and the
    wire then omits the field entirely.
    """

    kind: str
    url: str
    inline: str | None = None
    load: str | None = None

    def as_dict(self) -> dict[str, str]:
        """Return the wire form of the asset, carrying its inline body when set."""
        data = {keys.KIND: self.kind, keys.URL: self.url}
        if self.inline is not None:
            data[keys.INLINE] = self.inline
        if self.load is not None:
            data[keys.LOAD] = self.load
        return data


@dataclass(frozen=True, slots=True)
class FormMeta:
    """Machine-readable state of a form built from its field specs."""

    uid: str
    valid: bool
    errors: "Mapping[str, Sequence[str]]" = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the form meta object."""
        return {
            keys.UID: self.uid,
            keys.VALID: self.valid,
            keys.ERRORS: {name: list(msgs) for name, msgs in self.errors.items()},
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
    form: "FormMeta | None" = None
    csrf: "Mapping[str, Any] | None" = None
    request_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Return the wire form of the envelope as an ordered mapping."""
        data: dict[str, Any] = {
            keys.VERSION: self.version,
            keys.OPS: [op.as_dict() for op in self.ops],
            keys.ASSETS: [asset.as_dict() for asset in self.assets],
            keys.FORM: self.form.as_dict() if self.form is not None else None,
        }
        if self.csrf is not None:
            data[keys.CSRF] = dict(self.csrf)
        if self.request_id is not None:
            data[keys.REQUEST_ID] = self.request_id
        return data


__all__ = ["Asset", "Envelope", "FormMeta", "Patch"]
