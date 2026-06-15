"""System checks for the partial-rendering subsystem.

This module is excluded from coverage like every other area `checks.py`.
The zone checks read the same compiled page templates the renderer uses,
so a misconfigured zone is caught at `manage.py check` time rather than
on a partial request.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import Node, NodeList, TextNode
from django.template.defaulttags import ForNode, IfNode, WithNode

from next.checks.common import get_router_manager, iter_scanned_page_pairs
from next.components.backends import FileComponentsBackend
from next.components.manager import ComponentsManager
from next.conf import next_framework_settings
from next.pages import page

from .markers import ZoneNode


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.base import Template

    from next.urls import RouterBackend


E_DUPLICATE_ZONE: Final = "next.E060"
E_NON_ASCII_ZONE: Final = "next.E061"
E_ZONE_IN_FOR: Final = "next.E062"
E_ZONE_IN_IF: Final = "next.E063"
E_LAZY_WITHOUT_PLACEHOLDER: Final = "next.E064"
E_ZONE_IN_COMPONENT: Final = "next.E065"
E_UNREGISTERED_OP: Final = "next.E066"

W_WITH_OVER_ZONE: Final = "next.W067"
W_FORM_BACKEND_NOT_AWARE: Final = "next.W068"
W_MANIFEST_VERSION_NO_STORAGE: Final = "next.W069"


CHECK_IDS: Final = (
    E_DUPLICATE_ZONE,
    E_NON_ASCII_ZONE,
    E_ZONE_IN_FOR,
    E_ZONE_IN_IF,
    E_LAZY_WITHOUT_PLACEHOLDER,
    E_ZONE_IN_COMPONENT,
    E_UNREGISTERED_OP,
    W_WITH_OVER_ZONE,
    W_FORM_BACKEND_NOT_AWARE,
    W_MANIFEST_VERSION_NO_STORAGE,
)


_ZONE_SLUG = re.compile(r"\A[A-Za-z0-9_-]+\Z")


def _iter_composed_pages() -> "Iterator[tuple[Path, Template]]":
    """Yield each page path with its compiled composed template.

    Pages whose body is produced dynamically by `render()` have no
    static composed template and are skipped. A page that fails to
    compile is left to the page checks that own that failure.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    seen: set[Path] = set()
    for router in router_manager._backends:
        yield from _iter_router_pages(router, seen)


def _iter_router_pages(
    router: "RouterBackend",
    seen: set[Path],
) -> "Iterator[tuple[Path, Template]]":
    """Yield compiled composed templates for one router's scanned pages."""
    for _url_path, page_path in iter_scanned_page_pairs(router):
        resolved = page_path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        if not page.has_template(page_path):
            continue
        try:
            template = page.composed_template_for(page_path)
        except (TemplateSyntaxError, TemplateDoesNotExist, OSError, ValueError):
            continue
        yield page_path, template


def _zone_nodes(template: "Template") -> list[ZoneNode]:
    """Return every zone node of a compiled template."""
    return cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))


def _child_nodelists(node: Node) -> "Iterator[NodeList]":
    """Yield each declared child node list of a node."""
    for attr in node.child_nodelists:
        nodelist = getattr(node, attr, None)
        if isinstance(nodelist, NodeList):
            yield nodelist


def _significant(nodelist: NodeList) -> list[Node]:
    """Return the nodes of a list that are not pure whitespace text."""
    out: list[Node] = []
    for node in nodelist:
        if isinstance(node, TextNode) and not node.s.strip():
            continue
        out.append(node)
    return out


@register(Tags.templates)
def check_duplicate_zone_names(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when two zones in one composed page share a name (`next.E060`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        counts: dict[str, int] = {}
        for node in _zone_nodes(template):
            counts[node.name] = counts.get(node.name, 0) + 1
        for name, count in counts.items():
            if count < 2:  # noqa: PLR2004
                continue
            messages.append(
                Error(
                    f'Zone "{name}" is declared {count} times in the composed '
                    f"page template for {page_path}. Zone names address one node "
                    "each, so they must be unique across the layout chain and "
                    "the page body.",
                    obj=str(page_path),
                    id=E_DUPLICATE_ZONE,
                )
            )
    return messages


@register(Tags.templates)
def check_zone_name_is_slug(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a zone name is not an ASCII slug (`next.E061`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        for node in _zone_nodes(template):
            if _ZONE_SLUG.match(node.name):
                continue
            messages.append(
                Error(
                    f'Zone name "{node.name}" in {page_path} is not an ASCII '
                    "slug. Zone names travel in the X-Next-Zone header, which "
                    "is latin-1. Use letters, digits, hyphens, or underscores.",
                    obj=str(page_path),
                    id=E_NON_ASCII_ZONE,
                )
            )
    return messages


@register(Tags.templates)
def check_zone_not_in_loop(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a zone sits inside a `{% for %}` loop (`next.E062`)."""
    return _ancestor_check(
        ancestor=ForNode,
        check_id=E_ZONE_IN_FOR,
        reason=(
            "A standalone zone render does not see loop variables, so the zone "
            "cannot be re-rendered on its own. Move the zone out of the loop or "
            "wrap each item in a component."
        ),
    )


@register(Tags.templates)
def check_zone_not_in_if(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a zone sits inside an `{% if %}` block (`next.E063`)."""
    return _ancestor_check(
        ancestor=IfNode,
        check_id=E_ZONE_IN_IF,
        reason=(
            "A standalone zone render does not evaluate the enclosing "
            "condition, so the zone's visibility cannot be honoured. Move the "
            "condition inside the zone body instead."
        ),
    )


def _ancestor_check(
    *,
    ancestor: type[Node],
    check_id: str,
    reason: str,
) -> list[CheckMessage]:
    """Return errors for every zone nested under an `ancestor` node type."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        messages.extend(
            Error(
                f'Zone "{zone_name}" in {page_path} is nested inside a '
                f"{_TAG_LABELS[ancestor]} block. {reason}",
                obj=str(page_path),
                id=check_id,
            )
            for zone_name in _zones_under(template.nodelist, ancestor)
        )
    return messages


_TAG_LABELS: Final[dict[type[Node], str]] = {
    ForNode: "{% for %}",
    IfNode: "{% if %}",
}


def _zones_under(
    nodelist: NodeList,
    ancestor: type[Node],
    *,
    inside: bool = False,
) -> "Iterator[str]":
    """Yield names of zones reached while an `ancestor` node is on the path."""
    for node in nodelist:
        if isinstance(node, ZoneNode):
            if inside:
                yield node.name
            continue
        now_inside = inside or isinstance(node, ancestor)
        for child in _child_nodelists(node):
            yield from _zones_under(child, ancestor, inside=now_inside)


@register(Tags.templates)
def check_lazy_zone_has_placeholder(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a lazy zone declares no `{% placeholder %}` (`next.E064`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        for node in _zone_nodes(template):
            if node.lazy is None or _significant(node.placeholder):
                continue
            messages.append(
                Error(
                    f'Lazy zone "{node.name}" in {page_path} has no '
                    "{% placeholder %} branch. A lazy zone shows its placeholder "
                    "until the body arrives, so the branch is required.",
                    obj=str(page_path),
                    id=E_LAZY_WITHOUT_PLACEHOLDER,
                )
            )
    return messages


@register(Tags.templates)
def check_with_directly_over_zone(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Warn when a `{% with %}` wraps a zone directly (`next.W067`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        messages.extend(
            DjangoWarning(
                f'Zone "{zone_name}" in {page_path} sits directly inside a '
                "{% with %} block. The with-bindings are not visible to a "
                "standalone zone render. Move the bindings into a context "
                "provider or inside the zone body.",
                obj=str(page_path),
                id=W_WITH_OVER_ZONE,
            )
            for zone_name in _zones_directly_in_with(template.nodelist)
        )
    return messages


def _zones_directly_in_with(nodelist: NodeList) -> "Iterator[str]":
    """Yield zone names that are direct children of a `{% with %}` block."""
    for node in nodelist:
        if isinstance(node, WithNode):
            for child in _significant(node.nodelist):
                if isinstance(child, ZoneNode):
                    yield child.name
        for child_list in _child_nodelists(node):
            yield from _zones_directly_in_with(child_list)


@register(Tags.templates)
def check_no_zone_in_component(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Error when a component template declares a zone (`next.E065`)."""
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return []
    messages: list[CheckMessage] = []
    manager = ComponentsManager()
    manager._reload_config()
    seen: set[Path] = set()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        backend._ensure_loaded()
        for info in backend._registry:
            template_path = info.template_path
            if template_path is None:
                continue
            resolved = template_path.resolve()
            if resolved in seen or not resolved.exists():
                continue
            seen.add(resolved)
            messages.extend(_component_zone_errors(resolved))
    return messages


def _component_zone_errors(template_path: Path) -> list[CheckMessage]:
    """Return an error for each zone tag found in a component template file."""
    try:
        source = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if "{% zone " not in source and "{%zone " not in source:
        return []
    return [
        Error(
            f"Component template {template_path} declares a {{% zone %}} tag. "
            "A zone belongs to a page, not a component. Move the zone into the "
            "page or layout template.",
            obj=str(template_path),
            id=E_ZONE_IN_COMPONENT,
        )
    ]


__all__ = [
    "CHECK_IDS",
    "E_DUPLICATE_ZONE",
    "E_LAZY_WITHOUT_PLACEHOLDER",
    "E_NON_ASCII_ZONE",
    "E_UNREGISTERED_OP",
    "E_ZONE_IN_COMPONENT",
    "E_ZONE_IN_FOR",
    "E_ZONE_IN_IF",
    "W_FORM_BACKEND_NOT_AWARE",
    "W_MANIFEST_VERSION_NO_STORAGE",
    "W_WITH_OVER_ZONE",
    "check_duplicate_zone_names",
    "check_lazy_zone_has_placeholder",
    "check_no_zone_in_component",
    "check_with_directly_over_zone",
    "check_zone_name_is_slug",
    "check_zone_not_in_if",
    "check_zone_not_in_loop",
]
