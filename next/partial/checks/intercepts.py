"""System checks for the zones an intercept page serves over its target.

A zone set reads the composed template, so a page that fails to compose is skipped.
"""

from collections.abc import Collection, Mapping
from pathlib import Path

from django.core.checks import CheckMessage, Error, Tags, register
from django.template import Template

from next.checks import NEXT
from next.pages import page
from next.pages.checks.intercepts import (
    ServedIntercept,
    joined,
    served_intercepts,
    targeted_intercepts,
)
from next.pages.checks.modules import load_page, serves_nothing
from next.pages.loaders import LayoutTemplateLoader, has_body
from next.partial.registry import COMPOSE_FAILURES, ZoneInfo, standalone_zones, zones_of
from next.partial.zone import ZoneNode
from next.ports import action_owners_slot

from .codes import E_INTERCEPT_WITHOUT_ZONE, E_INTERCEPT_ZONE_SHARED


def _composed(page_path: Path) -> Template | None:
    """Return the composed template of a page, `None` where it does not compose.

    A compile failure is left to `next.E072`.
    """
    try:
        return page.composed_template_for(page_path)
    except COMPOSE_FAILURES:
        return None


def _layout_zone_names(page_path: Path) -> frozenset[str] | None:
    """Return the zones the layouts around a page declare, `None` where they fail."""
    loader = LayoutTemplateLoader()
    if not loader.can_load(page_path):
        return frozenset()
    try:
        skeleton = Template(loader.compose_skeleton(page_path))
    except COMPOSE_FAILURES:
        return None
    nodes = skeleton.nodelist.get_nodes_by_type(ZoneNode)
    return frozenset(node.name for node in nodes if isinstance(node, ZoneNode))


def own_zones(page_path: Path) -> Mapping[str, ZoneInfo] | None:
    """Return the zones the body of a page declares, nested ones included.

    The zones of its layouts are left out, since the target shares them.
    """
    template = _composed(page_path)
    layouts = _layout_zone_names(page_path)
    if template is None or layouts is None:
        return None
    declared = zones_of(template, page_path)
    return {name: info for name, info in declared.items() if name not in layouts}


def _target_zones(page_path: Path) -> frozenset[str] | None:
    """Return the standalone zones of a target, the ones it keeps from an intercept."""
    try:
        return frozenset(standalone_zones(page_path))
    except COMPOSE_FAILURES:
        return None


def _served_zones(page_path: Path) -> tuple[str, ...]:
    """Return the standalone zones of an intercept body, the ones it can serve."""
    zones = own_zones(page_path) or {}
    return tuple(name for name, info in zones.items() if info.standalone)


def _shared_with_target(
    served: ServedIntercept, mine: Collection[str]
) -> CheckMessage | None:
    """Return the error for zones an intercept shares with its target, if any."""
    theirs = _target_zones(served.target)
    if not mine or theirs is None:
        return None
    shared = theirs.intersection(mine)
    if not shared:
        return None
    return Error(
        f"The intercept page {served.hit.file} declares the zones {joined(shared)}, "
        f"which its target {served.target} declares too. The target serves a zone "
        "it declares, so the intercept never serves these.",
        hint="Rename the zone in the intercept and in the links that open it.",
        obj=str(served.hit.file),
        id=E_INTERCEPT_ZONE_SHARED,
    )


@register(Tags.templates, NEXT)
def check_intercept_zones_unique(*args, **kwargs) -> list[CheckMessage]:
    """Flag an intercept zone its target or a sibling intercept declares (`next.E173`).

    Of two intercepts sharing a zone the one later in file order never serves it.
    """
    errors: list[CheckMessage] = []
    claimed: dict[tuple[Path, str], Path] = {}
    for served in sorted(served_intercepts(), key=lambda served: served.hit.file):
        mine = _served_zones(served.hit.file)
        shared = _shared_with_target(served, mine)
        if shared is not None:
            errors.append(shared)
        taken: dict[Path, list[str]] = {}
        for name in mine:
            first = claimed.setdefault((served.target, name), served.hit.file)
            if first != served.hit.file:
                taken.setdefault(first, []).append(name)
        errors.extend(
            Error(
                f"The intercept pages {first} and {served.hit.file} both serve the "
                f"zones {joined(names)} over {served.target}. The first "
                "in file order wins, so the second never serves them.",
                hint="Give each intercept of one target zones of its own.",
                obj=str(served.hit.file),
                id=E_INTERCEPT_ZONE_SHARED,
            )
            for first, names in taken.items()
        )
    return errors


def _carries_nothing(served: ServedIntercept, owners: Collection[Path]) -> bool:
    """Whether a body-less intercept `page.py` passes nothing to the pages below it.

    One that passes access or context down, or owns an action, is a declaration carrier.
    """
    file = served.hit.file
    return serves_nothing(file, load_page(file)) and file not in owners


def _body_error(
    served: ServedIntercept, owners: Collection[Path]
) -> CheckMessage | None:
    """Return the error for an intercept with no template or no standalone zone."""
    file = served.hit.file
    if not has_body(file, served.module):
        if not _carries_nothing(served, owners):
            return None
        return Error(
            f"The intercept page {file} has neither a template.djx nor a template "
            "string and passes nothing to the pages below, so it declares no zone "
            "to serve over its target.",
            hint=(
                'Give it a template that wraps its content in {% zone "name" %}, '
                "or pass its access or context down with inherit=True."
            ),
            obj=str(file),
            id=E_INTERCEPT_WITHOUT_ZONE,
        )
    zones = own_zones(file)
    if zones is None or any(info.standalone for info in zones.values()):
        return None
    return Error(
        f"The intercept page {file} declares no zone it can render on its own, so "
        "no zone request over its target ever selects it.",
        hint=(
            'Wrap its content in {% zone "name" %} outside any component that '
            'binds it, and open it with data-next-layer="name".'
        ),
        obj=str(file),
        id=E_INTERCEPT_WITHOUT_ZONE,
    )


@register(Tags.templates, NEXT)
def check_intercept_bodies(*args, **kwargs) -> list[CheckMessage]:
    """Flag an intercept page with no template or no standalone zone (`next.E174`).

    A body-less `page.py` passing declarations down is spared, as `next.E012` spares it.
    """
    owners = action_owners_slot.get().page_files()
    return [
        error
        for served in targeted_intercepts()
        if (error := _body_error(served, owners)) is not None
    ]


__all__ = ["check_intercept_bodies", "check_intercept_zones_unique", "own_zones"]
