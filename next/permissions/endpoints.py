"""The access every page, action and component answers to, read without a request.

`manage.py permissions` and `manage.py routes` print it, and tools read it the same way.
"""

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from django.template import TemplateSyntaxError
from django.urls import Resolver404, resolve

from next.components.sources import get_components_manager, reached_components
from next.discovery import discover_page_registrations
from next.forms.manager import form_action_manager
from next.pages.access import middleware_login, owner_access, page_access
from next.pages.loaders import PAGE_MODULE
from next.pages.manager import page
from next.pages.paths import page_template_path
from next.urls.routes import collect_routes

from .access import declared
from .markers import OPEN, AccessRule, Requirement, joined


if TYPE_CHECKING:
    from next.components.info import ComponentInfo
    from next.forms.backends import ActionMeta
    from next.urls.routes import Route


type EndpointKind = Literal["page", "action", "component"]


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A page, an action or a component with the access rule it answers to.

    `closed` marks what `LoginRequiredMiddleware` refuses to an anonymous user.
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
            return "login (middleware)"
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


def _page_path(route: "Route") -> Path:
    """Return the `page.py` path a route serves, also for a virtual page."""
    return route.file.with_name(PAGE_MODULE)


def page_endpoint(route: "Route") -> Endpoint:
    """Return the access of the page a route serves, its `proxy.py` chain included."""
    held = page_access(_page_path(route))
    rule = held.rule
    closed = middleware_login(rule)
    return Endpoint("page", route.url, route.file, rule, closed, held.broken)


def _guard_rule(meta: "ActionMeta", source: Path) -> AccessRule:
    """Return what the `Meta` guard of an action requires, bound to its file."""
    guard = meta.get("guard")
    if guard is None:
        return OPEN
    requirements = [Requirement("login", source)]
    if guard.permissions:
        requirements.append(Requirement("permission", source, guard.permissions))
    return AccessRule(tuple(requirements), sources=(source,))


def action_endpoint(meta: "ActionMeta") -> Endpoint:
    """Return the access an action owes to its owner and its own guard.

    The rule of the posted origin page joins it per request and stays out.
    """
    source = Path(meta["file_path"])
    owner = owner_access(source)
    rules = [_guard_rule(meta, source)]
    public = False
    broken = None
    if owner is not None:
        rules.insert(0, owner.rule)
        public = owner.rule.public
        broken = owner.broken
    rule = joined(rules, public=public)
    return Endpoint(
        "action", meta["name"], source, rule, middleware_login(rule), broken
    )


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


def _import_components() -> None:
    """Run every `component.py`, so each component has declared its access."""
    for backend in get_components_manager().backends:
        backend.import_component_modules()


def _components() -> tuple["ComponentInfo", ...]:
    """Return every component once each `component.py` ran its declarations."""
    _import_components()
    infos: dict[ComponentInfo, None] = {}
    for backend in get_components_manager().backends:
        infos.update(dict.fromkeys(backend.iter_components()))
    return tuple(infos)


def _page_components(page_path: Path) -> tuple["ComponentInfo", ...] | None:
    """Return the components the composed page template renders, `None` if broken."""
    _import_components()
    try:
        template = page.composed_template_for(page_path)
    except TemplateSyntaxError:
        return None
    return reached_components(template.nodelist, page_template_path(page_path))


def _owned(meta: "ActionMeta", page_path: Path) -> bool:
    """Return whether an action is declared in the directory of `page_path`."""
    return Path(meta["file_path"]).parent == page_path.parent


def resolve_page(url: str) -> Path | None:
    """Return the `page.py` path the URL resolves to, `None` for no page."""
    try:
        match = resolve(urlsplit(url).path or "/")
    except Resolver404:
        return None
    found = getattr(match.func, "next_page_path", None)
    return found if isinstance(found, Path) else None


def endpoints(page_path: Path | None = None) -> tuple[Endpoint, ...]:
    """Return every page, action and declaring component, or those of one page.

    A page keeps the actions of its directory and the components its template renders.
    """
    routes = collect_routes()
    if page_path is None:
        pages = tuple(page_endpoint(route) for route in routes)
        return (
            *pages,
            *(action_endpoint(meta) for meta in _actions()),
            *_declaring(_components()),
        )
    pages = tuple(
        page_endpoint(route) for route in routes if _page_path(route) == page_path
    )
    actions = tuple(meta for meta in _actions() if _owned(meta, page_path))
    infos = _page_components(page_path)
    if infos is None:
        pages = tuple(
            replace(found, broken=found.broken or page_path) for found in pages
        )
    return (
        *pages,
        *(action_endpoint(meta) for meta in actions),
        *_declaring(infos or ()),
    )


def _declaring(infos: tuple["ComponentInfo", ...]) -> tuple[Endpoint, ...]:
    """Return the endpoint of each component in `infos` that declares access."""
    found = (_component_endpoint(info) for info in infos)
    return tuple(endpoint for endpoint in found if endpoint is not None)


__all__ = ["Endpoint", "endpoints", "page_endpoint", "resolve_page"]
