"""System checks on the JSON-LD graph the metadata declares."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Error, Tags, register
from django.core.serializers.json import DjangoJSONEncoder
from django.utils.timezone import is_naive

from next.checks import NEXT, SEO
from next.pages.metadata.ld import ID, TYPE, plain

from .pages import folded_pages, loaded_metadata_pages
from .scope import declared_segments


if TYPE_CHECKING:
    from .scope import DeclaredSegment


_ENOUGH_TYPES: Final = 2


def _objects(value: object) -> Iterator[Mapping[str, object]]:
    """Yield every JSON object inside `value`, itself first."""
    if isinstance(value, Mapping):
        yield value
        for item in value.values():
            yield from _objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from _objects(item)


def _naive_time(value: object) -> bool:
    """Whether a datetime without a time zone sits anywhere in `value`."""
    if isinstance(value, datetime):
        return is_naive(value)
    if isinstance(value, Mapping):
        return any(_naive_time(item) for item in value.values())
    if isinstance(value, list):
        return any(_naive_time(item) for item in value)
    return False


def _serialise_problem(node: object) -> str | None:
    """Return why one node cannot render as JSON-LD, `None` when it can."""
    if _naive_time(node):
        return "holds a datetime without a time zone"
    try:
        json.dumps(node, cls=DjangoJSONEncoder, allow_nan=False)
    except (TypeError, ValueError) as exc:
        return f"does not serialise, {exc}"
    return None


def _segment_messages(item: DeclaredSegment) -> list[CheckMessage]:
    return [
        Error(
            f"{item.source} declares jsonld[{index}], which {problem}. "
            "Give it finite numbers, aware datetimes and JSON values.",
            obj=item.obj,
            id="next.E127",
        )
        for index, node in enumerate(item.segment.metadata.jsonld)
        if (problem := _serialise_problem(plain(node))) is not None
    ]


def _id_types(graph: list[object]) -> dict[str, set[str]]:
    """Map every `@id` of the graph to the types the nodes naming it declare."""
    found: dict[str, set[str]] = {}
    for obj in _objects(graph):
        ident, kind = obj.get(ID), obj.get(TYPE)
        if isinstance(ident, str) and kind is not None:
            found.setdefault(ident, set()).add(str(kind))
    return found


@register(Tags.templates, NEXT, SEO)
def check_metadata_jsonld(*args, **kwargs) -> list[CheckMessage]:
    """Validate every declared JSON-LD node and the graph each page folds."""
    init_errors, pages = loaded_metadata_pages()
    messages = list(init_errors)
    for item in declared_segments(pages):
        messages.extend(_segment_messages(item))
    for entry, meta in folded_pages(pages):
        graph: list[object] = [plain(node) for node in meta.jsonld]
        for ident, kinds in sorted(_id_types(graph).items()):
            if len(kinds) < _ENOUGH_TYPES:
                continue
            messages.append(
                Error(
                    f"{entry.page_path} folds the JSON-LD @id {ident!r} as "
                    f"{', '.join(sorted(kinds))}, one node under two types. Give "
                    "each node its own @id.",
                    obj=str(entry.page_path),
                    id="next.E127",
                )
            )
    return messages


__all__ = ["check_metadata_jsonld"]
