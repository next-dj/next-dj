"""System checks for intercepting routes, the pages that serve a zone over a target.

The router drops an intercept it cannot serve without a word, so these name each one.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, NamedTuple

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import RunMemo, get_router_manager, iter_scanned_pages
from next.pages.access import anonymous_admitted, default_login, page_access, routed
from next.pages.errors import PageMetadataShapeError
from next.pages.loaders import load_page_module, serves
from next.pages.manager import page
from next.pages.metadata.normalize import normalize_metadata
from next.ports import router_access_slot
from next.utils import (
    LAYOUT_TEMPLATE,
    ROUTE_BRACKET_PATTERN,
    normalise_route_name,
    parse_intercept,
    strip_route_groups,
)

from .metadata.crumbs import declares_crumb
from .walk import tree_walk


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from next.urls import RouterManager

    from .walk import TreeIntercept


_MARKER_HINT: Final = (
    "Point the marker at a directory that serves a page, (.) beside the intercept, "
    "one (..) per level up, or (...) from the tree root."
)


class ServedIntercept(NamedTuple):
    """One intercept page whose target its own tree routes, with its module if any.

    A lone `template.djx` is a virtual page with no module.
    """

    hit: TreeIntercept
    target: Path
    module: types.ModuleType | None


def _read_routed_trails(router_manager: RouterManager) -> dict[Path, dict[str, Path]]:
    """Return the routed pages of every tree by trail, keyed by the resolved tree."""
    trails: dict[Path, dict[str, Path]] = {}
    for router in router_manager.backends:
        for root, url_path, page_path in iter_scanned_pages(router):
            if routed(page_path):
                trails.setdefault(root.path.resolve(), {}).setdefault(
                    url_path, page_path
                )
    return trails


_routed_trails: RunMemo[dict[Path, dict[str, Path]]] = RunMemo()


def routed_trails() -> Mapping[Path, Mapping[str, Path]]:
    """Return the routed pages of every tree by trail, read once per run."""
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return {}
    return _routed_trails.get(
        router_manager, lambda: _read_routed_trails(router_manager)
    )


def target_of(hit: TreeIntercept) -> Path | None:
    """Return the page of its own tree an intercept serves over, `None` for none."""
    return routed_trails().get(hit.root.path.resolve(), {}).get(hit.target_trail)


def _read_targeted() -> tuple[ServedIntercept, ...]:
    """Return every intercept page that loads and whose target its tree routes."""
    found: list[ServedIntercept] = []
    for hit in tree_walk().intercepts:
        target = target_of(hit)
        if target is None:
            continue
        module, error = load_page_module(hit.file)
        if error is None:
            found.append(ServedIntercept(hit, target, module))
    return tuple(found)


_targeted: RunMemo[tuple[ServedIntercept, ...]] = RunMemo()


def targeted_intercepts() -> Iterator[ServedIntercept]:
    """Yield every intercept page that loads and whose target its tree routes.

    A broken `page.py` is left to the import check, a missing target to `next.E170`.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    yield from _targeted.get(router_manager, _read_targeted)


def served_intercepts() -> Iterator[ServedIntercept]:
    """Yield the targeted intercept pages with a body, the only ones a zone selects.

    A body-less `page.py` only passes declarations down to the intercepts below it.
    """
    for served in targeted_intercepts():
        if serves(served.hit.file, served.module, None):
            yield served


def joined(names: Iterable[str]) -> str:
    """Return names quoted and joined in name order."""
    return ", ".join(repr(name) for name in sorted(names))


def _parameter(segment: str) -> str | None:
    """Return the URL kwarg a bracket segment captures, `None` for a literal one."""
    match = ROUTE_BRACKET_PATTERN.fullmatch(segment)
    if match is None:
        return None
    raw = match["wild"] if match["wild"] is not None else match["param"]
    return normalise_route_name(raw.rpartition(":")[2].strip())


def _own_trail(raw_trail: str) -> str:
    """Return an intercept directory trail with its groups and markers left out."""
    return "/".join(
        parsed[1] if (parsed := parse_intercept(segment)) else segment
        for segment in strip_route_groups(raw_trail).split("/")
    )


def _spelled_apart(trail: str, other: str) -> bool:
    """Whether two trails differ only in how equally named parameters are spelled."""
    left, right = trail.split("/"), other.split("/")
    if len(left) != len(right) or trail == other:
        return False
    return all(
        mine == theirs
        or ((name := _parameter(mine)) is not None and name == _parameter(theirs))
        for mine, theirs in zip(left, right, strict=True)
    )


def _target_hint(hit: TreeIntercept) -> str:
    """Return why the trail of an intercept matches no page, and how to point it."""
    trails = routed_trails()
    own = hit.root.path.resolve()
    near = sorted(
        candidate
        for candidate in trails.get(own, {})
        if _spelled_apart(hit.target_trail, candidate)
    )
    if near:
        return (
            f"The tree routes {near[0]!r}, which spells the same parameters "
            "differently. Spell the bracket directories of the intercept as the "
            "target does."
        )
    other = sorted(
        str(tree)
        for tree, held in trails.items()
        if tree != own and hit.target_trail in held
    )
    if other:
        return (
            f"The page tree {other[0]} routes it, but an intercept serves only over "
            "a page of its own tree. Move the intercept into that tree."
        )
    return _MARKER_HINT


@register(NEXT)
def check_intercept_targets(*args, **kwargs) -> list[CheckMessage]:
    """Flag an intercept whose target its own tree does not route (`next.E170`).

    A body-less `page.py` only passes declarations down, so it targets nothing.
    """
    return [
        Error(
            f"The intercept page {hit.file} targets the trail {hit.target_trail!r}, "
            f"which no page of its tree {hit.root.path} serves, so the router drops "
            "it and it never serves a zone.",
            hint=_target_hint(hit),
            obj=str(hit.file),
            id="next.E170",
        )
        for hit in tree_walk().intercepts
        if target_of(hit) is None and serves(hit.file, *load_page_module(hit.file))
    ]


@register(NEXT)
def check_intercept_scope(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `(..)` intercept that climbs above its tree root (`next.E171`)."""
    errors: list[CheckMessage] = []
    for directory in tree_walk().directories:
        name = directory.path.name
        if directory.within is not None or directory.url_trail is not None:
            continue
        if parse_intercept(name) is None:
            continue
        scope = directory.scope_trail
        errors.append(
            Error(
                f"The intercept {directory.path} climbs above its tree root, since "
                f"its (..) markers count more levels than the URL trail {scope!r} "
                "of its parent holds, so the router drops it.",
                hint=(
                    "Use one (..) per level up to the target, or (...) to target "
                    "from the tree root."
                ),
                obj=str(directory.path),
                id="next.E171",
            )
        )
    return errors


@register(NEXT)
def check_nested_intercepts(*args, **kwargs) -> list[CheckMessage]:
    """Flag an intercept directory inside another intercept (`next.E172`)."""
    return [
        Error(
            f"The intercept {directory.path} sits inside the intercept "
            f"{directory.within.source}, and the router reads no intercept nested "
            "in another, so it never serves a zone.",
            hint="Move it out beside the outer intercept, over the page it targets.",
            obj=str(directory.path),
            id="next.E172",
        )
        for directory in tree_walk().directories
        if directory.within is not None
        and parse_intercept(directory.path.name) is not None
    ]


@register(NEXT)
def check_intercept_render(*args, **kwargs) -> list[CheckMessage]:
    """Flag an intercept page that defines `render()` (`next.E175`).

    An intercept answers zone requests alone, which render its template and skip it.
    """
    return [
        Error(
            f"The intercept page {served.hit.file} defines render(), which never "
            "runs, since an intercept serves zone requests only and renders the "
            "zone from its template.",
            hint=(
                "Move the work into @page.context callables, or into the render() "
                "of the target page."
            ),
            obj=str(served.hit.file),
            id="next.E175",
        )
        for served in served_intercepts()
        if callable(getattr(served.module, "render", None))
    ]


def _kwarg_errors(served: ServedIntercept) -> list[CheckMessage]:
    """Return the error for the URL kwargs an intercept sits under and its target lacks.

    Both sides are read by the router's own parser, so a mixed segment counts.
    """
    hit = served.hit
    access = router_access_slot.get()
    captured = access.page_kwargs(hit.root, hit.target_trail)
    if captured is None:
        return []
    own = access.page_kwargs(hit.root, _own_trail(hit.raw_trail))
    missing = frozenset() if own is None else own - captured
    if not missing:
        return []
    return [
        Error(
            f"The intercept page {hit.file} sits under the bracket directories "
            f"{joined(missing)}, which its target trail {hit.target_trail!r} "
            "does not capture, so a zone request passes no such kwarg to its "
            "context or its access.",
            hint=(
                "Target a page whose URL captures them, or move the intercept "
                "out from under those directories."
            ),
            obj=str(hit.file),
            id="next.E176",
        )
    ]


@register(NEXT)
def check_intercept_url_kwargs(*args, **kwargs) -> list[CheckMessage]:
    """Flag a URL kwarg an intercept sits under that its target lacks (`next.E176`).

    A zone request carries the target kwargs alone, and `next.E162` judges its `obj=`.
    """
    errors: list[CheckMessage] = []
    for served in served_intercepts():
        errors.extend(_kwarg_errors(served))
    return errors


def _inert_declarations(served: ServedIntercept) -> list[str]:
    """Return what an intercept page declares that its zone response never carries."""
    found: list[str] = []
    raw, _entry = page.metadata_declaration(served.hit.file)
    if isinstance(raw, Mapping):
        try:
            segment = normalize_metadata(raw, source=str(served.hit.file))
        except PageMetadataShapeError:
            segment = None
        if declares_crumb(segment):
            found.append("a breadcrumb")
    found.extend(
        name
        for name in ("headers", "cache")
        if getattr(served.module, name, None) is not None
    )
    return found


@register(NEXT)
def check_intercept_inert_declarations(*args, **kwargs) -> list[CheckMessage]:
    """Warn about what an intercept page declares in vain (`next.W138`).

    A zone response keeps the headers of its target, is never cached, gives no crumb.
    """
    warnings: list[CheckMessage] = []
    for served in served_intercepts():
        inert = _inert_declarations(served)
        if not inert:
            continue
        listed = ", ".join(inert[:-1])
        named = f"{listed} and {inert[-1]}" if listed else inert[0]
        warnings.append(
            DjangoWarning(
                f"The intercept page {served.hit.file} declares {named}, which "
                "never take effect. Its zone response carries the headers of its "
                "target, is never cached and builds no breadcrumbs.",
                hint="Drop them from the intercept, or declare them on the target.",
                obj=str(served.hit.file),
                id="next.W138",
            )
        )
    return warnings


@register(NEXT)
def check_intercept_layouts(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a `layout.djx` inside an intercept directory (`next.W139`)."""
    warnings: list[CheckMessage] = []
    seen: set[Path] = set()
    for directory in tree_walk().directories:
        scope = directory.scope
        if scope is None or directory.url_trail is None:
            continue
        layout = directory.path / LAYOUT_TEMPLATE
        if layout in seen or not layout.is_file():
            continue
        seen.add(layout)
        warnings.append(
            DjangoWarning(
                f"{layout} sits inside the intercept {scope.source}, and no layout "
                "wraps a zone response, so its markup never renders.",
                hint=(
                    "Move the markup into the intercept template, or into a layout "
                    "above the intercept."
                ),
                obj=str(layout),
                id="next.W139",
            )
        )
    return warnings


@register(NEXT)
def check_intercept_default_login(*args, **kwargs) -> list[CheckMessage]:
    """Warn about an intercept asking a login over a public target (`next.W140`).

    The stricter chain wins, so the modal answers a login redirect over an open page.
    """
    warnings: list[CheckMessage] = []
    for served in served_intercepts():
        access = page_access(served.hit.file)
        if access.broken is not None or not default_login(access.rule):
            continue
        if not anonymous_admitted(page_access(served.target)):
            continue
        warnings.append(
            DjangoWarning(
                f"The intercept page {served.hit.file} declares no access, so the "
                f"default login applies to it, while its target {served.target} "
                "admits anonymous users. An anonymous visitor opening the modal "
                "gets a login redirect over a public page.",
                hint=(
                    "Declare page.public() in the intercept page.py, or "
                    "page.login_required() to keep the login on purpose."
                ),
                obj=str(served.hit.file),
                id="next.W140",
            )
        )
    return warnings


__all__ = [
    "ServedIntercept",
    "check_intercept_default_login",
    "check_intercept_inert_declarations",
    "check_intercept_layouts",
    "check_intercept_render",
    "check_intercept_scope",
    "check_intercept_targets",
    "check_intercept_url_kwargs",
    "check_nested_intercepts",
    "joined",
    "routed_trails",
    "served_intercepts",
    "target_of",
    "targeted_intercepts",
]
