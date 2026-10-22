"""The access every page, action and component answers to, read without a request.

`manage.py permissions` and `manage.py routes` print it, and tools read it the same way.
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from django.template import TemplateSyntaxError
from django.urls import (
    Resolver404,
    ResolverMatch,
    URLPattern,
    URLResolver,
    get_resolver,
    get_script_prefix,
    resolve,
)

from next.components.sources import (
    get_components_manager,
    import_component_modules,
    reached_components,
)
from next.discovery import discover_page_registrations
from next.forms.dispatch.permissions import guard_rule, owner_rule
from next.forms.manager import form_action_manager
from next.pages.access import default_login, page_access
from next.pages.loaders import PAGE_MODULE
from next.pages.manager import page
from next.pages.paths import page_template_path
from next.partial.registry import COMPOSE_FAILURES, standalone_zones
from next.permissions.access import declared, login_middleware
from next.permissions.markers import AccessRule, Requirement, joined
from next.urls.routes import collect_routes, with_intercepts


if TYPE_CHECKING:
    from next.components.info import ComponentInfo
    from next.forms.backends import ActionMeta
    from next.urls.routes import Route


type EndpointKind = Literal["page", "intercept", "action", "component"]

type Includes = Sequence[URLResolver]


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A page, an action or a component with the access rule it answers to.

    `closed` marks what the default login refuses to an anonymous user.
    """

    kind: EndpointKind
    name: str
    file: Path
    rule: AccessRule
    closed: bool = False
    broken: Path | None = None

    @property
    def access(self) -> str:
        """Return the rule in short words, each once, as the `manage.py` tables do."""
        if self.broken is not None:
            return "broken"
        requirements = self.rule.requirements
        if requirements:
            words = (word for item in requirements for word in _words(item))
            return ", ".join(dict.fromkeys(words))
        if self.rule.public:
            return "public"
        if self.closed:
            return "login (default)"
        return "-"

    @property
    def sources(self) -> tuple[Path, ...]:
        """Return the files the requirements come from, each once, root first.

        A public rule names the file whose `public()` opened it instead.
        """
        if self.broken is not None:
            return (self.broken,)
        requirements = self.rule.requirements
        if requirements:
            return tuple(dict.fromkeys(item.source for item in requirements))
        if self.rule.public:
            return self.rule.sources
        return ()


def _words(requirement: Requirement) -> tuple[str, ...]:
    """Spell one requirement, a login or one `perm` per permission it names."""
    if requirement.kind == "login":
        return ("login",)
    obj = requirement.obj
    if obj is None:
        suffix = ""
    elif isinstance(obj, str):
        suffix = f" on {obj}"
    else:
        suffix = f" on {obj._meta.label}"
    return tuple(f"perm {perm}{suffix}" for perm in requirement.perms)


@dataclass(frozen=True, slots=True)
class ViewEndpoint:
    """A view of the URLconf outside the page trees, as the login middleware sees it.

    `exempt` is `login_not_required`, and `open` marks a framework view open to anyone.
    """

    route: str
    view: str
    exempt: bool
    closed: bool
    open: bool = False
    deferred: str | None = None

    @property
    def access(self) -> str:
        """Return the access in the words the `manage.py permissions` table prints.

        An unmarked view reads `?`, since Django `login_required` leaves no mark on it.
        """
        if self.deferred is not None:
            return f"per {self.deferred}"
        if self.open:
            return "public"
        if self.exempt:
            return "no login"
        if self.closed:
            return "login (middleware)"
        return "?"


def _route_piece(pattern: URLResolver | URLPattern) -> str:
    """Return the route one pattern adds, a regex stripped of its anchors."""
    return str(pattern.pattern).removeprefix("^").removesuffix("$")


def _view_endpoint(includes: Includes, pattern: URLPattern) -> ViewEndpoint | None:
    """Return the view a pattern under `includes` routes to, `None` for a page."""
    callback = pattern.callback
    if getattr(callback, "next_page_path", None) is not None:
        return None
    exempt = not getattr(callback, "login_required", True)
    pieces = [*map(_route_piece, includes), _route_piece(pattern)]
    closed = not exempt and login_middleware() is not None
    return ViewEndpoint(
        "/" + "".join(pieces),
        pattern.lookup_str,
        exempt,
        closed,
        open=exempt and getattr(callback, "next_open", False),
        deferred=getattr(callback, "next_access_kind", None),
    )


def _patterns(
    patterns: Sequence[URLResolver | URLPattern], includes: Includes = ()
) -> Iterator[tuple[Includes, URLPattern]]:
    """Yield each view pattern with the includes leading from the URLconf root to it."""
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _patterns(pattern.url_patterns, (*includes, pattern))
        else:
            yield includes, pattern


def view_endpoints() -> tuple[ViewEndpoint, ...]:
    """Return every view the URLconf routes outside the page trees, in its order."""
    found = (
        _view_endpoint(includes, pattern)
        for includes, pattern in _patterns(get_resolver().url_patterns)
    )
    return tuple(view for view in found if view is not None)


def _page_path(route: "Route") -> Path:
    """Return the `page.py` path a route serves, also for a virtual page."""
    return route.file.with_name(PAGE_MODULE)


def page_endpoint(route: "Route") -> Endpoint:
    """Return the access of the page a route serves, the `page.py` chain included.

    An intercept row answers to its own chain and is named by its target and zones.
    """
    held = page_access(_page_path(route))
    rule = held.rule
    closed = default_login(rule)
    if route.intercept is None:
        return Endpoint("page", route.url, route.file, rule, closed, held.broken)
    name = f"{route.url} ({served_zones(route)})"
    return Endpoint("intercept", name, route.file, rule, closed, held.broken)


def served_zones(route: "Route") -> str:
    """Return the standalone zones an intercept row serves, joined in declaration order.

    A routed row reads blank, an intercept declaring none or failing to compose `-`.
    """
    if route.intercept is None:
        return ""
    try:
        zones = standalone_zones(route.intercept.file)
    except COMPOSE_FAILURES:
        return "-"
    return ", ".join(zones) or "-"


def action_endpoint(meta: "ActionMeta") -> Endpoint:
    """Return the access an action owes to its owner and its own guard.

    The rule of the posted origin page joins it per request and stays out.
    """
    source = Path(meta["file_path"])
    owner = owner_rule(source)
    if owner is None:
        rule = guard_rule(meta)
        broken = None
    else:
        rule = joined([owner.rule, guard_rule(meta)], public=owner.rule.public)
        broken = owner.broken
    return Endpoint("action", meta["name"], source, rule, default_login(rule), broken)


def _component_endpoint(info: "ComponentInfo") -> Endpoint | None:
    """Return the access a `component.py` declares, `None` when it declares none."""
    module_path = info.module_path
    if module_path is None:
        return None
    rule = declared(module_path)
    if not rule.requirements:
        return None
    return Endpoint("component", info.name, module_path, rule)


def _actions() -> tuple["ActionMeta", ...]:
    """Return every registered action once the page modules declaring them ran."""
    discover_page_registrations()
    return tuple(
        meta
        for backend in form_action_manager.backends
        for meta in backend.iter_actions()
    )


def _components() -> tuple["ComponentInfo", ...]:
    """Return every component once each `component.py` ran its declarations."""
    import_component_modules()
    infos: dict[ComponentInfo, None] = {}
    for backend in get_components_manager().backends:
        infos.update(dict.fromkeys(backend.iter_components()))
    return tuple(infos)


def _page_components(page_path: Path) -> tuple["ComponentInfo", ...] | None:
    """Return the components the composed page template renders, `None` if broken."""
    import_component_modules()
    try:
        template = page.composed_template_for(page_path)
    except TemplateSyntaxError:
        return None
    return reached_components(template.nodelist, page_template_path(page_path))


def _owned(meta: "ActionMeta", page_path: Path) -> bool:
    """Return whether an action is declared in the directory of `page_path`."""
    return Path(meta["file_path"]).parent == page_path.parent


def _match(url: str) -> ResolverMatch | None:
    """Return what the URLconf resolves the path of `url` to, `None` for a 404.

    The path reads with or without its leading slash and the script prefix.
    """
    path = "/" + urlsplit(url).path.lstrip("/")
    prefix = get_script_prefix()
    if prefix != "/" and path.startswith(prefix):
        path = "/" + path.removeprefix(prefix)
    try:
        return resolve(path)
    except Resolver404:
        return None


def resolve_page(url: str) -> Path | None:
    """Return the `page.py` path the URL resolves to, `None` for no page."""
    match = _match(url)
    found = None if match is None else getattr(match.func, "next_page_path", None)
    return found if isinstance(found, Path) else None


def resolve_view(url: str) -> ViewEndpoint | None:
    """Return the view the URL resolves to, `None` for a page or a 404.

    The last tried chain of a match is the one that matched, the URLconf root left out.
    """
    match = _match(url)
    if match is None or not match.tried:
        return None
    *includes, pattern = match.tried[-1]
    return _view_endpoint(includes, pattern)


def endpoints(page_path: Path | None = None) -> tuple[Endpoint, ...]:
    """Return every page, action and declaring component, or those of one page.

    A page keeps its intercepts, the actions beside it or them, and its components.
    """
    routes = with_intercepts(collect_routes())
    if page_path is None:
        pages = tuple(page_endpoint(route) for route in routes)
        actions = _actions()
        infos: tuple[ComponentInfo, ...] | None = _components()
    else:
        infos = _page_components(page_path)
        rows = tuple(route for route in routes if _target_path(route) == page_path)
        pages = tuple(_page_row(route, broken=infos is None) for route in rows)
        owners = dict.fromkeys((page_path, *map(_page_path, rows)))
        declared = _actions()
        actions = tuple(
            meta for owner in owners for meta in declared if _owned(meta, owner)
        )
    return (
        *pages,
        *(action_endpoint(meta) for meta in actions),
        *_declaring(infos or ()),
    )


def _target_path(route: "Route") -> Path:
    """Return the `page.py` path of the routed page a row lists under."""
    rule = route.intercept
    return _page_path(route) if rule is None else rule.target_file


def _page_row(route: "Route", *, broken: bool) -> Endpoint:
    """Return the endpoint of a row, a routed page marked broken when `broken` holds."""
    found = page_endpoint(route)
    if not broken or route.intercept is not None:
        return found
    return replace(found, broken=found.broken or _page_path(route))


def _declaring(infos: tuple["ComponentInfo", ...]) -> tuple[Endpoint, ...]:
    """Return the endpoint of each component in `infos` that declares access."""
    found = (_component_endpoint(info) for info in infos)
    return tuple(endpoint for endpoint in found if endpoint is not None)


__all__ = [
    "Endpoint",
    "ViewEndpoint",
    "endpoints",
    "page_endpoint",
    "resolve_page",
    "resolve_view",
    "served_zones",
    "view_endpoints",
]
