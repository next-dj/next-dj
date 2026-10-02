"""System checks on the JSON-LD graph the metadata declares."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Error, Tags, register

from next.checks import NEXT, SEO
from next.pages.metadata.backends import dump_jsonld
from next.pages.metadata.ld import ID, TYPE, iter_json, node_id, to_json

from .pages import folded_pages, loaded_metadata_pages
from .scope import declared_segments


if TYPE_CHECKING:
    from .scope import DeclaredSegment


_ENOUGH_TYPES: Final = 2


def _serialise_problem(node: object) -> str | None:
    """Return why one node cannot render as JSON-LD, `None` when it can.

    The node goes through the walk and the dump the renderer runs, so the two agree.
    """
    try:
        dump_jsonld(to_json(node, ids=node_id))
    except (TypeError, ValueError) as exc:
        return f"does not serialise, {exc}"
    return None


def _segment_messages(item: DeclaredSegment) -> list[CheckMessage]:
    return [
        Error(
            f"{item.source} declares jsonld[{index}], which {problem}. "
            "Give it finite numbers and JSON values.",
            obj=item.obj,
            id="next.E127",
        )
        for index, node in enumerate(item.segment.metadata.jsonld)
        if (problem := _serialise_problem(node)) is not None
    ]


def _graph(nodes: tuple[object, ...]) -> list[object]:
    """Return the nodes in JSON form, one `next.E127` reports left out."""
    graph: list[object] = []
    for node in nodes:
        try:
            graph.append(to_json(node, ids=node_id))
        except ValueError:
            continue
    return graph


def _id_types(graph: list[object]) -> dict[str, set[str]]:
    """Map every `@id` of the graph to the types the nodes naming it declare."""
    found: dict[str, set[str]] = {}
    for _path, obj in iter_json(graph):
        if not isinstance(obj, Mapping):
            continue
        ident, kind = obj.get(ID), obj.get(TYPE)
        if isinstance(ident, str) and kind is not None:
            found.setdefault(ident, set()).add(str(kind))
    return found


@register(Tags.templates, NEXT, SEO)
def check_metadata_jsonld(*args, **kwargs) -> list[CheckMessage]:
    """Validate every declared JSON-LD node (`next.E127`) and each folded graph.

    One `@id` under two types in a folded graph earns `next.E101`.
    """
    pages = loaded_metadata_pages()
    messages: list[CheckMessage] = []
    for item in declared_segments(pages):
        messages.extend(_segment_messages(item))
    for entry, meta in folded_pages(pages):
        for ident, kinds in sorted(_id_types(_graph(meta.jsonld)).items()):
            if len(kinds) < _ENOUGH_TYPES:
                continue
            messages.append(
                Error(
                    f"{entry.page_path} folds the JSON-LD @id {ident!r} as "
                    f"{', '.join(sorted(kinds))}, one node under two types. Give "
                    "each node its own @id.",
                    obj=str(entry.page_path),
                    id="next.E101",
                )
            )
    return messages


__all__ = ["check_metadata_jsonld"]
