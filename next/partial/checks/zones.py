"""System checks for the `{% zone %}` tags of a composed page.

The ids are `next.E060` to `next.E065` for a zone that is duplicated, misnamed, badly
nested, lazy without a placeholder or declared in a component, plus `next.E078` for a
`@context(zone=)` naming no zone and `next.W067` for a `{% with %}` over one.
"""

import re
from typing import TYPE_CHECKING, Final

from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template.base import Node
from django.template.defaulttags import ForNode, IfNode

from next.checks import NEXT
from next.components.sources import get_components_manager
from next.conf import next_framework_settings
from next.pages import page
from next.pages.checks.composed import iter_composed_pages

from .codes import (
    E_CONTEXT_ZONE_UNKNOWN,
    E_DUPLICATE_ZONE,
    E_LAZY_WITHOUT_PLACEHOLDER,
    E_NON_ASCII_ZONE,
    E_ZONE_IN_COMPONENT,
    E_ZONE_IN_FOR,
    E_ZONE_IN_IF,
    W_WITH_OVER_ZONE,
)
from .nodes import significant, zone_nodes, zones_directly_in_with, zones_under


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.pages.registry import ZoneBinding


_MIN_DUPLICATE_COUNT: Final = 2

_ZONE_SLUG = re.compile(r"\A[A-Za-z0-9_-]+\Z")

_TAG_LABELS: Final[dict[type[Node], str]] = {ForNode: "{% for %}", IfNode: "{% if %}"}


@register(Tags.templates, NEXT)
def check_duplicate_zone_names(*args, **kwargs) -> list[CheckMessage]:
    """Error when two zones in one composed page share a name (`next.E060`)."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        counts: dict[str, int] = {}
        for node in zone_nodes(template):
            counts[node.name] = counts.get(node.name, 0) + 1
        for name, count in counts.items():
            if count < _MIN_DUPLICATE_COUNT:
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


@register(Tags.templates, NEXT)
def check_zone_name_is_slug(*args, **kwargs) -> list[CheckMessage]:
    """Error when a zone name is not an ASCII slug (`next.E061`)."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        for node in zone_nodes(template):
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


@register(Tags.templates, NEXT)
def check_zone_not_in_loop(*args, **kwargs) -> list[CheckMessage]:
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


@register(Tags.templates, NEXT)
def check_zone_not_in_if(*args, **kwargs) -> list[CheckMessage]:
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
    *, ancestor: type[Node], check_id: str, reason: str
) -> list[CheckMessage]:
    """Return errors for every zone nested under an `ancestor` node type."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        messages.extend(
            Error(
                f'Zone "{zone_name}" in {page_path} is nested inside a '
                f"{_TAG_LABELS[ancestor]} block. {reason}",
                obj=str(page_path),
                id=check_id,
            )
            for zone_name in zones_under(template.nodelist, ancestor)
        )
    return messages


@register(Tags.templates, NEXT)
def check_lazy_zone_has_placeholder(*args, **kwargs) -> list[CheckMessage]:
    """Error when a lazy zone declares no `{% placeholder %}` (`next.E064`)."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        for node in zone_nodes(template):
            if node.options.lazy is None or significant(node.placeholder):
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


@register(Tags.templates, NEXT)
def check_with_directly_over_zone(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a `{% with %}` wraps a zone directly (`next.W067`)."""
    messages: list[CheckMessage] = []
    for page_path, template in iter_composed_pages():
        messages.extend(
            DjangoWarning(
                f'Zone "{zone_name}" in {page_path} sits directly inside a '
                "{% with %} block. The with-bindings are not visible to a "
                "standalone zone render. Move the bindings into a context "
                "provider or inside the zone body.",
                obj=str(page_path),
                id=W_WITH_OVER_ZONE,
            )
            for zone_name in zones_directly_in_with(template.nodelist)
        )
    return messages


@register(Tags.templates, NEXT)
def check_context_zone_names_exist(*args, **kwargs) -> list[CheckMessage]:
    """Error when a `@context(zone=)` names an undeclared zone (`next.E078`).

    A full render runs every callable, so a misspelt zone name looks healthy there and
    silently drops the callable from every zone request instead.
    """
    messages: list[CheckMessage] = []
    # The composed-template walk imports each `page.py`, so read bindings after.
    composed = list(iter_composed_pages())
    bindings = page.zone_bindings()
    for page_path, template in composed:
        page_bindings = bindings.get(page_path, ())
        if not any(binding.zones for binding in page_bindings):
            continue
        declared = {node.name for node in zone_nodes(template)}
        sentence = _declared_zones_sentence(sorted(declared))
        for label, zone_name in _zone_bound_contexts(page_bindings):
            if zone_name in declared:
                continue
            messages.append(
                Error(
                    f"The @context {label} in {page_path} binds to zone "
                    f'"{zone_name}", which the composed page template does not '
                    "declare. No zone request ever matches the callable, so its "
                    f"value is missing from every zone render. {sentence}",
                    obj=str(page_path),
                    id=E_CONTEXT_ZONE_UNKNOWN,
                )
            )
    return messages


def _declared_zones_sentence(declared: list[str]) -> str:
    """Return the sentence naming the zones a composed page template declares."""
    if not declared:
        return "The page declares no zones."
    names = ", ".join(repr(name) for name in declared)
    return f"Declared zones: {names}."


def _zone_bound_contexts(
    bindings: "tuple[ZoneBinding, ...]",
) -> "Iterator[tuple[str, str]]":
    """Yield the label and bound zone name of every zone-bound `@context` of a page.

    The registry keys on the file declaring the callable, which for a `page.py` is the
    path the page scan walked, so the composed-page path looks the bindings up directly.
    """
    for binding in bindings:
        if binding.zones is None:
            continue
        label = (
            binding.name
            if binding.key is None
            else f'{binding.name} (key "{binding.key}")'
        )
        for zone_name in sorted(binding.zones):
            yield label, zone_name


@register(Tags.templates, NEXT)
def check_no_zone_in_component(*args, **kwargs) -> list[CheckMessage]:
    """Error when a component template declares a zone (`next.E065`)."""
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return []
    messages: list[CheckMessage] = []
    manager = get_components_manager()
    seen: set[Path] = set()
    for backend in manager.backends:
        for info in backend.iter_components():
            template_path = info.template_path
            if template_path is None:
                continue
            resolved = template_path.resolve()
            if resolved in seen or not resolved.exists():
                continue
            seen.add(resolved)
            messages.extend(_component_zone_errors(resolved))
    return messages


def _component_zone_errors(template_path: "Path") -> list[CheckMessage]:
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
    "check_context_zone_names_exist",
    "check_duplicate_zone_names",
    "check_lazy_zone_has_placeholder",
    "check_no_zone_in_component",
    "check_with_directly_over_zone",
    "check_zone_name_is_slug",
    "check_zone_not_in_if",
    "check_zone_not_in_loop",
]
