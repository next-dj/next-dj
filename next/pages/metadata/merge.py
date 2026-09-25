"""Shallow fold of the metadata segment chain into one immutable value."""

from collections.abc import Iterable
from dataclasses import fields
from typing import Any, Final

from .placeholders import apply_title_template
from .schema import Metadata, Segment, Text


MERGED_FIELDS: Final[tuple[str, ...]] = tuple(
    field.name for field in fields(Metadata) if field.name != "title"
)
"""The fields the nearer segment replaces whole, which is every one but the title."""


def _unset(value: object) -> bool:
    """Whether a segment field was left at its default, without comparing a Promise."""
    return value is None or (isinstance(value, tuple) and not value)


def _fold_fields(chain: tuple[Segment, ...]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for segment in chain:
        for name in MERGED_FIELDS:
            value = getattr(segment, name)
            if not _unset(value):
                merged[name] = value
    return merged


def _fold_title(chain: tuple[Segment, ...], *, site_name: Text | None) -> Text | None:
    title: Text | None = None
    template: Text | None = None
    for segment in chain:
        spec = segment.title
        if spec is None:
            continue
        if spec.absolute is not None:
            title = spec.absolute
        elif spec.text is not None:
            title = apply_title_template(template, spec.text, site_name=site_name)
        elif spec.default is not None:
            title = spec.default
        if spec.template is not None:
            template = spec.template
    return title


def fold_metadata(segments: Iterable[Segment]) -> Metadata:
    """Fold the chain from root to leaf, the nearer segment winning per field.

    Its `{site_name}` reads the site name the whole chain settled on.
    """
    chain = tuple(segments)
    merged = _fold_fields(chain)
    merged["title"] = _fold_title(chain, site_name=merged.get("site_name"))
    return Metadata(**merged)


__all__ = ["MERGED_FIELDS", "fold_metadata"]
