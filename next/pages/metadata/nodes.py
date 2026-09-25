"""The template node `{% metadata %}` compiles to, rendering the head tags of a page."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple, cast, override

from django.template.base import Node

from next.seeding import METADATA_KEY

from .backends import render_metadata
from .chain import MetadataThunk


if TYPE_CHECKING:
    from django.template.context import Context

    from .schema import Metadata


class _Resolved(NamedTuple):
    """The metadata one template render folded, with the thunk it came from."""

    thunk: MetadataThunk
    metadata: Metadata


class MetadataNode(Node):
    """Renders the head tags of the page whose metadata thunk the context carries."""

    @override
    def render(self, context: Context) -> str:
        """Resolve the thunk against this context, once per template render.

        A zone override reaches the callables, and outside a page render it is empty.
        """
        thunk = context.get(METADATA_KEY)
        if not isinstance(thunk, MetadataThunk):
            return ""
        metadata = thunk.folded()
        if metadata is None:
            metadata = self._resolve(context, thunk)
        return render_metadata(metadata, request=thunk.request)

    @staticmethod
    def _resolve(context: Context, thunk: MetadataThunk) -> Metadata:
        """Run the chain callables on the flattened context, memoised on the render."""
        state = context.render_context
        memo = state.get(METADATA_KEY)
        if isinstance(memo, _Resolved) and memo.thunk is thunk:
            return memo.metadata
        metadata = thunk.resolve(cast("dict[str, object]", context.flatten()))
        state[METADATA_KEY] = _Resolved(thunk, metadata)
        return metadata


__all__ = ["MetadataNode"]
