"""System checks for parenthesised directory names and the route groups they spell."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.utils import (
    is_intercept_name,
    is_route_group,
    list_page_dir,
    parse_intercept,
    routes_into,
)

from .walk import tree_walk


if TYPE_CHECKING:
    from pathlib import Path

    from .walk import TreeDirectory


_GROUP_HINT: Final = (
    "Name a route group (identifier), a letter or '_' first, then letters, digits, "
    "'_' or '-', like (marketing) or (shop-admin)."
)
_INTERCEPT_HINT: Final = (
    "Spell an intercept as one marker then the segment it targets, like (.)photo, "
    "(..)photo or (...)photo, the marker kinds never mixed."
)
_CACHE_DIR: Final = "__pycache__"


def _is_parenthesised(name: str) -> bool:
    """Whether a directory name carries a parenthesis the router may read."""
    return "(" in name or ")" in name


def _name_error(directory: TreeDirectory) -> CheckMessage:
    """Return the `next.E169` error for one directory name the router cannot read."""
    name = directory.path.name
    if is_intercept_name(name):
        return Error(
            f"The directory {directory.path} is named {name!r}. The (.), (..) and "
            "(...) prefixes are reserved for intercepting routes, and this name "
            "parses as none, so the router skips the directory and every page in it.",
            hint=_INTERCEPT_HINT,
            obj=str(directory.path),
            id="next.E169",
        )
    return Error(
        f"The directory {directory.path} is named {name!r}, which is neither a "
        "route group nor an intercept, so the router routes it literally, "
        "parentheses and all.",
        hint=_GROUP_HINT,
        obj=str(directory.path),
        id="next.E169",
    )


@register(NEXT)
def check_parenthesised_directory_names(*args, **kwargs) -> list[CheckMessage]:
    """Flag a parenthesised directory name the router cannot read (`next.E169`).

    A name is a route group `(identifier)` or a valid intercept, nothing in between.
    """
    return [
        _name_error(directory)
        for directory in tree_walk().directories
        if _is_parenthesised(name := directory.path.name)
        and not is_route_group(name)
        and parse_intercept(name) is None
    ]


def _lone_child(directory: TreeDirectory) -> Path | None:
    """Return the one routed child of a group that holds nothing else, `None` otherwise.

    A file of any kind counts as a contribution, only a hidden one is passed over.
    """
    listing = list_page_dir(directory.path)
    if any(not name.startswith(".") for name in listing.files):
        return None
    children = [child for child in listing.children if child.name != _CACHE_DIR]
    if len(children) != 1:
        return None
    child = children[0]
    if not routes_into(child.name, directory.skip_names):
        return None
    return None if is_intercept_name(child.name) else child


@register(NEXT)
def check_pointless_route_groups(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a route group that wraps one directory and adds nothing (`next.W137`).

    The group leaves the URL, so without a layout, page or components it does nothing.
    """
    warnings: list[CheckMessage] = []
    for directory in tree_walk().directories:
        if not is_route_group(directory.path.name):
            continue
        child = _lone_child(directory)
        if child is None:
            continue
        warnings.append(
            DjangoWarning(
                f"The route group {directory.path} holds only {child.name!r} and "
                "adds no layout, page, components or other file, and a group leaves "
                "the URL, so it changes nothing for the pages below it.",
                hint=(
                    f"Move {child.name!r} up a level and drop the group, or give "
                    "the group the layout.djx or page.py its pages share."
                ),
                obj=str(directory.path),
                id="next.W137",
            )
        )
    return warnings


__all__ = ["check_parenthesised_directory_names", "check_pointless_route_groups"]
