"""A head assertion that reads back the tags `{% metadata %}` rendered."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Final

from next.pages.metadata.head import HeadTags, head_tags


if TYPE_CHECKING:
    from django.http import HttpResponse


_NAMED: Final = ("description", "keywords", "viewport", "robots", "googlebot")
_PREFIXED: Final = {"og": ("property", "og:"), "twitter": ("name", "twitter:")}
_KEYS: Final = frozenset(
    {"title", "canonical", "alternates", "jsonld", *_NAMED, *_PREFIXED}
)


def _prefixed(
    tags: HeadTags, block: str, expected: Mapping[str, object]
) -> dict[str, str | None]:
    attribute, prefix = _PREFIXED[block]
    found = tags.properties if attribute == "property" else tags.names
    return {key: found.get(f"{prefix}{key}") for key in expected}


def _actual(tags: HeadTags, key: str, expected: object) -> object:
    if key in _NAMED:
        return tags.names.get(key)
    if key in _PREFIXED:
        return _prefixed(tags, key, expected if isinstance(expected, Mapping) else {})
    return getattr(tags, key)


def _wanted(key: str, expected: object) -> object:
    if isinstance(expected, Mapping):
        return dict(expected)
    if key == "jsonld" and isinstance(expected, Sequence):
        return list(expected)
    return expected


def assert_metadata(response: HttpResponse | str, **expected: object) -> None:
    """Assert the head tags of a response, `None` expecting a tag to be absent.

    `og` and `twitter` take property suffixes, `alternates` and `jsonld` whole values.
    """
    unknown = sorted(set(expected) - _KEYS)
    if unknown:
        msg = f"assert_metadata() got unknown keys {unknown}, expected {sorted(_KEYS)}"
        raise TypeError(msg)
    html = response if isinstance(response, str) else response.content.decode()
    tags = head_tags(html)
    mismatches = [
        f"{key}: expected {want!r}, got {got!r}"
        for key, value in expected.items()
        if (got := _actual(tags, key, value)) != (want := _wanted(key, value))
    ]
    if mismatches:
        raise AssertionError("\n".join(mismatches))


__all__ = ["HeadTags", "assert_metadata", "head_tags"]
