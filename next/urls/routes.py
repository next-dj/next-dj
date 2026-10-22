"""The route table of the file router, one row per page in resolution order."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.urls import URLPattern

from next.conf import next_framework_settings
from next.intercept import intercepts
from next.pages.loaders import PAGE_MODULE
from next.utils import PageRoot, walk_page_tree

from .backends import FileRouterBackend
from .errors import URLParameterError
from .manager import router_manager
from .mount import tree_router
from .parser import default_url_parser
from .urlconf import outer_kwargs, tree_route, urlconf_mounts


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from next.intercept import Intercept


@dataclass(frozen=True, slots=True)
class Route:
    """One routed page, with its absolute route, its namespaced name and its file.

    An intercept row carries its rule, the URL of its target and no name.
    """

    url: str
    name: str
    file: Path
    intercept: Intercept | None = None


@dataclass(frozen=True, slots=True)
class PageRoute:
    """The full route one page answers and every kwarg a match of it hands the page."""

    url: str
    kwargs: frozenset[str]


def _route_url(root: PageRoot, trail: str, base: str) -> tuple[str, dict[str, str]]:
    """Return the URL a page at `trail` answers and the parameters it captures."""
    pattern, parameters = default_url_parser.parse_url_pattern(trail)
    return f"/{tree_route(root, base)}{pattern}", parameters


def page_route(root: PageRoot, trail: str) -> PageRoute | None:
    """Return the route a page at `trail` of `root` answers, `None` if refused."""
    mounts = urlconf_mounts()
    try:
        url, parameters = _route_url(root, trail, mounts.include_point or "")
    except URLParameterError:
        return None
    return PageRoute(url=url, kwargs=outer_kwargs(root, mounts) | frozenset(parameters))


def _page_file(file_path: Path) -> Path:
    """Return the `page.py` of a page, or the `template.djx` of a virtual one."""
    return file_path if file_path.exists() else file_path.with_name("template.djx")


def _tree_routes(
    root: PageRoot, base: str, skip_names: frozenset[str], routed: frozenset[Path]
) -> Iterator[Route]:
    """Yield a row per routed page of `root`, an auto one under the include `base`."""
    template = next_framework_settings.URL_NAME_TEMPLATE
    for url_path, file_path in walk_page_tree(root.path, skip_names):
        if file_path not in routed:
            continue
        url, _parameters = _route_url(root, url_path, base)
        name = default_url_parser.prepare_url_name(url_path)
        yield Route(
            url=url,
            name=f"{root.namespace}:{template.format(name=name)}",
            file=_page_file(file_path),
        )


def _routed_pages(first: FileRouterBackend, mounted: list[PageRoot]) -> frozenset[Path]:
    """Return the page files the built patterns route."""
    patterns = [
        *router_manager,
        *(
            pattern
            for root in mounted
            for pattern in first.generate_tree_urls(root.path)
        ),
    ]
    return frozenset(
        page_path
        for pattern in patterns
        if isinstance(pattern, URLPattern)
        and (page_path := getattr(pattern.callback, "next_page_path", None)) is not None
    )


def collect_routes() -> list[Route]:
    """Return a row per page the file routers serve, in resolution order."""
    first = tree_router()
    if first is None:
        return []
    mounts = urlconf_mounts()
    mounted = [root for root in first.page_roots() if root.prefix is not None]
    routed = _routed_pages(first, mounted)
    base = mounts.include_point
    auto = (
        []
        if base is None
        else [
            route
            for router in router_manager.backends
            if isinstance(router, FileRouterBackend)
            for root in router.page_roots()
            if root.prefix is None
            for route in _tree_routes(root, base, router.skip_dir_names(), routed)
        ]
    )
    split = mounts.include_index
    skip_names = first.skip_dir_names()

    def mounted_rows(roots: list[PageRoot]) -> Iterator[Route]:
        for root in roots:
            yield from _tree_routes(root, "", skip_names, routed)

    return [*mounted_rows(mounted[:split]), *auto, *mounted_rows(mounted[split:])]


def with_intercepts(routes: Iterable[Route]) -> list[Route]:
    """Return `routes` with a row per intercept rule right after the row of its target.

    The rules are read as published, so `routes` comes from `collect_routes` first.
    """
    rows: list[Route] = []
    for route in routes:
        rows.append(route)
        rows.extend(
            Route(url=route.url, name="", file=_page_file(rule.file), intercept=rule)
            for rule in intercepts.of(route.file.with_name(PAGE_MODULE))
        )
    return rows


__all__ = ["PageRoute", "Route", "collect_routes", "page_route", "with_intercepts"]
