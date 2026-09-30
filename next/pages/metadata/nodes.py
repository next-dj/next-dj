"""The template nodes `{% metadata %}` and `{% breadcrumbs %}` compile to."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, NamedTuple, cast, override

from django.template.base import Node
from django.utils.html import format_html, format_html_join
from django.utils.safestring import SafeString

from next.seeding import METADATA_KEY

from .backends import metadata_renderer
from .chain import MetadataThunk
from .resolve import publish_metadata, resolve_metadata


if TYPE_CHECKING:
    from collections.abc import Iterable

    from django.template.context import Context

    from .markers import Breadcrumb, ResolvedMetadata


_NAV: Final = '<nav aria-label="Breadcrumb"><ol>{}</ol></nav>'
_CURRENT: Final = ' aria-current="page"'


class _Resolved(NamedTuple):
    """The metadata one template render resolved, with the thunk it came from."""

    thunk: MetadataThunk
    resolved: ResolvedMetadata


def context_metadata(context: Context) -> ResolvedMetadata | None:
    """Fold and resolve once per template render, publishing on the request.

    The memo sits in the root render-context layer, which an `{% include %}` shares.
    """
    thunk = context.get(METADATA_KEY)
    if not isinstance(thunk, MetadataThunk):
        return None
    state = cast("dict[str, object]", context.render_context.dicts[0])
    memo = state.get(METADATA_KEY)
    if isinstance(memo, _Resolved) and memo.thunk is thunk:
        return memo.resolved
    meta = thunk.folded()
    if meta is None:
        meta = thunk.fold(cast("dict[str, object]", context.flatten()))
    request = thunk.request
    resolved = resolve_metadata(meta, request=request)
    state[METADATA_KEY] = _Resolved(thunk, resolved)
    if request is not None:
        publish_metadata(request, resolved)
    return resolved


class MetadataNode(Node):
    """Renders the head tags of the page whose metadata thunk the context carries."""

    @override
    def render(self, context: Context) -> str:
        """Render the resolve of the thunk, outside a page render nothing at all."""
        resolved = context_metadata(context)
        return "" if resolved is None else metadata_renderer().render(resolved)


def _crumb(crumb: Breadcrumb) -> SafeString:
    current = SafeString(_CURRENT) if crumb.current else ""
    if crumb.url is None:
        return format_html("<li{}>{}</li>", current, crumb.label)
    return format_html(
        '<li><a href="{}"{}>{}</a></li>', crumb.url, current, crumb.label
    )


def render_breadcrumbs(crumbs: Iterable[Breadcrumb]) -> SafeString:
    """Render the crumbs as an ordered list in a labelled `<nav>`, empty for none."""
    items = format_html_join("", "{}", ((_crumb(crumb),) for crumb in crumbs))
    return format_html(_NAV, items) if items else SafeString("")


class BreadcrumbsNode(Node):
    """Renders the breadcrumbs of the page, or stores them under `target`."""

    def __init__(self, target: str | None = None) -> None:
        """Remember the context name `as` names, `None` for the rendered form."""
        self.target = target

    @override
    def render(self, context: Context) -> str:
        """Render the trail, or bind the `Breadcrumb` tuple and render nothing."""
        resolved = context_metadata(context)
        crumbs = () if resolved is None else resolved.breadcrumbs
        if self.target is None:
            return render_breadcrumbs(crumbs)
        context[self.target] = crumbs
        return ""


__all__ = ["BreadcrumbsNode", "MetadataNode", "context_metadata", "render_breadcrumbs"]
