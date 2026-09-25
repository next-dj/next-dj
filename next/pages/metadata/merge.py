"""Shallow fold of the metadata segment chain into one immutable value."""

from collections.abc import Iterable
from dataclasses import fields
from typing import Any, Final

from django.utils.functional import lazy

from .placeholders import parse_template, substitute_title
from .schema import Metadata, Segment, Text


MERGED_FIELDS: Final[tuple[str, ...]] = tuple(
    field.name for field in fields(Metadata) if field.name != "title"
)
"""The fields the nearer segment replaces whole, which is every one but the title."""


def _title_or_bare(template: Text, text: Text, site_name: Text | None) -> str:
    """Fill the template, or answer the bare text when it wants an absent site name.

    The template is parsed after translation, so the decision has to wait for `str()`.
    """
    evaluated = str(template)
    if site_name is None and any(
        name == "site_name" for _, name in parse_template(evaluated)
    ):
        return str(text)
    return substitute_title(evaluated, {"title": text, "site_name": site_name})


_title_or_bare_lazy = lazy(_title_or_bare, str)


def apply_title_template(
    template: Text | None, text: Text, *, site_name: Text | None
) -> Text:
    """Return `text` under the chain template, still lazy until it is rendered."""
    if template is None:
        return text
    return _title_or_bare_lazy(template, text, site_name)


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

    The title walks the chain with the template in force, and its `{site_name}` reads
    the site name the whole chain settled on rather than the one in force at the time.
    """
    chain = tuple(segments)
    merged = _fold_fields(chain)
    merged["title"] = _fold_title(chain, site_name=merged.get("site_name"))
    return Metadata(**merged)


__all__ = ["MERGED_FIELDS", "apply_title_template", "fold_metadata"]
