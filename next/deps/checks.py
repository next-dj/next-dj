"""System checks for the `DObject` parameters a project declares.

Annotations, routes and registries alone are read, so no user code or query runs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import (
    RunMemo,
    discover_page_registrations,
    get_router_manager,
    iter_scanned_pages,
)
from next.components.context import component
from next.components.sources import get_components_manager
from next.conf import next_framework_settings
from next.forms.checks.sources import iter_registered_actions
from next.introspect import describe_callable
from next.pages.manager import page
from next.urls.routes import page_route

from .introspect import HINT_ERRORS, cached_signature, cached_type_hints
from .objects import HINT_ADVICE, SCOPE_DEPENDENCY, object_target, unresolved_object
from .resolver import current_resolver


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    from django.db.models import Model

    from next.pages.registry import PageContextEntry
    from next.urls import RouterManager
    from next.utils import PageRoot


_FORM_HOOKS = (
    "get_initial",
    "get_form_kwargs",
    "check_permissions",
    "has_object_permission",
    "on_valid",
)

_WIZARD_HOOKS = ("check_permissions", "done")


class _ObjectParam(NamedTuple):
    """One `DObject` parameter with the callable declaring it."""

    func: Callable[..., Any]
    name: str
    model: type[Model]
    parent: str | None


class _PageRun(NamedTuple):
    """A routed `page.py`, the kwargs its full route hands it, and its context order.

    A refused route keeps its tree trail and no kwargs, and ancestors lead `steps`.
    """

    route: str
    page_path: Path
    route_kwargs: frozenset[str] | None
    steps: tuple[tuple[str | None, PageContextEntry], ...]


class _ObjectSites(NamedTuple):
    """Every callable a `DObject` parameter can sit on, collected once per check run."""

    pages: tuple[_PageRun, ...]
    components: tuple[Callable[..., Any], ...]
    forms: tuple[Callable[..., Any], ...]
    dependencies: tuple[Callable[..., Any], ...]


def _object_params(func: Callable[..., Any]) -> tuple[_ObjectParam, ...]:
    """Return the `DObject` parameters of `func`, read the way the resolver reads them.

    An unreadable hint counts as absent, since the resolver falls back to the raw one.
    """
    try:
        hints = cached_type_hints(func)
    except HINT_ERRORS:
        return ()
    found: list[_ObjectParam] = []
    for name, hint in hints.items():
        target = None if name == "return" else object_target(hint)
        if target is not None:
            found.append(_ObjectParam(func, name, *target))
    return tuple(found)


def _unresolved_params(func: Callable[..., Any]) -> tuple[str, ...]:
    """Return the parameters of `func` whose unresolved string hint names `DObject`."""
    try:
        cached_type_hints(func)
    except HINT_ERRORS:
        pass
    else:
        return ()
    try:
        signature = cached_signature(func)
    except (ValueError, TypeError):
        return ()
    return tuple(
        name
        for name, param in signature.parameters.items()
        if unresolved_object(param.annotation)
    )


def _params_of(funcs: Iterable[Callable[..., Any]]) -> tuple[_ObjectParam, ...]:
    """Return the `DObject` parameters of every callable in `funcs`."""
    return tuple(param for func in funcs for param in _object_params(func))


def _page_run(root: PageRoot, url_path: str, page_path: Path) -> _PageRun:
    """Return the run order of every context callable a render of `page_path` calls."""
    registry = page._context_manager
    inherited = [
        step for group in registry._inheritable_groups(page_path) for step in group
    ]
    steps = (*inherited, *registry._page_order(page_path).entries)
    route = page_route(root, url_path)
    if route is None:
        return _PageRun(url_path, page_path, None, steps)
    return _PageRun(route.url, page_path, route.kwargs, steps)


def _loaded_pages(
    router_manager: RouterManager,
) -> Iterator[tuple[PageRoot, str, Path]]:
    """Yield each `page.py` that loaded once, with the tree the walk found it in."""
    loaded = {path for _url_path, path in discover_page_registrations(router_manager)}
    for router in router_manager.backends:
        for root, url_path, page_path in iter_scanned_pages(router):
            if page_path in loaded:
                loaded.discard(page_path)
                yield root, url_path, page_path


def _component_callables() -> Iterator[Callable[..., Any]]:
    """Yield every `@component.context` callable once the component modules ran."""
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return
    for backend in get_components_manager().backends:
        backend.import_component_modules()
    for entries in component._registry._registry.values():
        for entry in entries.values():
            yield entry.func


def _hooks(owner: object, names: Iterable[str]) -> Iterator[Callable[..., Any]]:
    """Yield the callables `owner` carries under `names`."""
    for name in names:
        hook = getattr(owner, name, None)
        if callable(hook):
            yield hook


def _form_class_callables(form_class: object) -> Iterator[Callable[..., Any]]:
    """Yield the DI-resolved hooks of a form class, or a form factory itself."""
    if isinstance(form_class, type):
        yield from _hooks(form_class, _FORM_HOOKS)
    elif callable(form_class):
        yield form_class


def _form_callables() -> Iterator[Callable[..., Any]]:
    """Yield every handler, form factory, form hook and wizard hook registered."""
    for meta in iter_registered_actions():
        handler = meta.get("handler")
        if handler is not None:
            yield handler
        yield from _form_class_callables(meta.get("form_class"))
        wizard_class = meta.get("wizard_class")
        if wizard_class is None:
            continue
        yield from _hooks(wizard_class, _WIZARD_HOOKS)
        for _step, step_class in wizard_class._static_steps():
            yield from _form_class_callables(step_class)


def _collect_sites(router_manager: RouterManager) -> _ObjectSites:
    """Read every place a `DObject` parameter can sit for one check run."""
    pages = tuple(
        _page_run(root, url_path, page_path)
        for root, url_path, page_path in _loaded_pages(router_manager)
    )
    return _ObjectSites(
        pages=pages,
        components=tuple(_component_callables()),
        forms=tuple(_form_callables()),
        dependencies=tuple(current_resolver().dependencies().values()),
    )


_sites: RunMemo[_ObjectSites] = RunMemo()


def object_sites() -> _ObjectSites | None:
    """Return the `DObject` sites of the project, or None when the routers failed.

    A router that fails to build is reported once by `next.E007`.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return None
    return _sites.get(router_manager, lambda: _collect_sites(router_manager))


def _all_callables(sites: _ObjectSites) -> Iterator[Callable[..., Any]]:
    """Yield every callable the sites hold, each once."""
    page_funcs = (entry.func for run in sites.pages for _key, entry in run.steps)
    seen: set[int] = set()
    for func in (*page_funcs, *sites.components, *sites.forms, *sites.dependencies):
        if id(func) not in seen:
            seen.add(id(func))
            yield func


def _all_params(sites: _ObjectSites) -> Iterator[_ObjectParam]:
    """Yield every `DObject` parameter the project declares."""
    for func in _all_callables(sites):
        yield from _object_params(func)


def _unique(messages: Iterable[CheckMessage]) -> list[CheckMessage]:
    """Return `messages` without repeats, since one callable may register many times."""
    reported: set[str] = set()
    unique: list[CheckMessage] = []
    for message in messages:
        if message.msg not in reported:
            reported.add(message.msg)
            unique.append(message)
    return unique


def _site(param: _ObjectParam) -> str:
    """Name the parameter and the callable declaring it for a message."""
    return f'parameter "{param.name}" of {describe_callable(param.func)}'


def _marker(param: _ObjectParam) -> str:
    """Spell the annotation of the parameter the way a message quotes it."""
    label = param.model._meta.label
    if param.parent is None:
        return f"DObject[{label}]"
    return f'DObject[{label}, "{param.parent}"]'


@register(NEXT)
def check_object_managers(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `DObject` model whose manager defines no `visible_to` (`next.E155`)."""
    sites = object_sites()
    if sites is None:
        return []
    errors: list[CheckMessage] = []
    checked: set[type[Model]] = set()
    for param in _all_params(sites):
        model = param.model
        if model in checked:
            continue
        checked.add(model)
        if callable(getattr(model._meta.default_manager, "visible_to", None)):
            continue
        errors.append(
            Error(
                f"{_marker(param)} on {_site(param)} loads its row through "
                f"{model._meta.label}._default_manager.visible_to(scope), which "
                "that manager does not define. Add visible_to(scope) to the "
                "default manager, returning the rows the scope may see.",
                obj=model,
                id="next.E155",
            )
        )
    return errors


def _provides(
    provided: dict[str, frozenset[str] | None],
    parent: str,
    zones: frozenset[str] | None,
) -> bool:
    """Whether the context holds `parent` on every render that runs a callable.

    A zone-bound value is absent on other zones, which an unbound callable also runs on.
    """
    if parent not in provided:
        return False
    held = provided[parent]
    return held is None or (zones is not None and zones <= held)


def _route_warning(run: _PageRun, param: _ObjectParam) -> DjangoWarning:
    """Warn about a page context `DObject` whose route captures no kwarg of its name.

    A warning, because the walk of the URLconf reads no include a lazy sequence hides.
    """
    captured = ", ".join(sorted(run.route_kwargs or ())) or "none"
    return DjangoWarning(
        f"{_marker(param)} on {_site(param)} looks its row up by the URL kwarg "
        f'"{param.name}", but the route "{run.route}" of {run.page_path} '
        f"captures {captured}. Rename the parameter after a captured kwarg, add "
        f"a [{param.name}] directory to the page path, or capture it in the "
        "include that mounts the tree.",
        obj=str(run.page_path),
        id="next.W128",
    )


def _parent_error(run: _PageRun, param: _ObjectParam) -> Error:
    """Report a page context `DObject` whose parent no earlier context provides."""
    return Error(
        f"{_marker(param)} on {_site(param)} filters by the context value "
        f'"{param.parent}", which no context callable running before it on the '
        f'"{run.route}" route provides. Declare it with '
        f'@context("{param.parent}", inherit_context=True) in an ancestor page.py.',
        obj=str(run.page_path),
        id="next.E156",
    )


def _page_run_messages(run: _PageRun) -> Iterator[CheckMessage]:
    """Yield the binding messages of every `DObject` on one page, in run order.

    A keyless callable spreads unknown keys, so a parent read after one goes unjudged.
    """
    provided: dict[str, frozenset[str] | None] = {}
    open_keys = False
    for key, entry in run.steps:
        for param in _object_params(entry.func):
            if run.route_kwargs is not None and param.name not in run.route_kwargs:
                yield _route_warning(run, param)
            parent = param.parent
            if parent is None or open_keys:
                continue
            if not _provides(provided, parent, entry.zones):
                yield _parent_error(run, param)
        if key is None:
            open_keys = True
        else:
            provided.setdefault(key, entry.zones)


@register(NEXT)
def check_object_page_bindings(*args, **kwargs) -> list[CheckMessage]:
    """Flag a page `DObject` with no route kwarg (`next.W128`) or parent (`next.E156`).

    Each page is walked in its own render order, inherited ancestor callables first.
    """
    sites = object_sites()
    if sites is None:
        return []
    return _unique(
        message for run in sites.pages for message in _page_run_messages(run)
    )


@register(NEXT)
def check_object_form_parents(*args, **kwargs) -> list[CheckMessage]:
    """Flag a parent `DObject` on a form handler or hook (`next.E157`).

    Form dispatch resolves with no page context, so the parent is never there.
    """
    sites = object_sites()
    if sites is None:
        return []
    return _unique(
        Error(
            f"{_marker(param)} on {_site(param)} filters by the context value "
            f'"{param.parent}", but a form handler or hook resolves with no page '
            "context. Drop the parent and look the row up by its own URL kwarg, "
            "or load it in a page context and read it there.",
            obj=describe_callable(param.func),
            id="next.E157",
        )
        for param in _params_of(sites.forms)
        if param.parent is not None
    )


@register(NEXT)
def check_object_component_contexts(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `DObject` on a `@component.context` callable (`next.E158`).

    A component resolves with no URL kwargs, so the lookup never finds its pk.
    """
    sites = object_sites()
    if sites is None:
        return []
    return _unique(
        Error(
            f"{_marker(param)} on {_site(param)} looks its row up by a URL "
            "kwarg, which a component context never receives. Load the row in "
            "a page context and read it from the template context by name.",
            obj=describe_callable(param.func),
            id="next.E158",
        )
        for param in _params_of(sites.components)
    )


@register(NEXT)
def check_object_scope(*args, **kwargs) -> list[CheckMessage]:
    """Flag `DObject` parameters with no `scope` dependency registered (`next.E159`).

    A project that declares no `DObject` needs no scope, so the check stays silent.
    """
    sites = object_sites()
    if sites is None:
        return []
    if current_resolver().get_dependency(SCOPE_DEPENDENCY) is not None:
        return []
    first = next(_all_params(sites), None)
    if first is None:
        return []
    return [
        Error(
            f"{_marker(first)} on {_site(first)} passes the "
            f'"{SCOPE_DEPENDENCY}" dependency to visible_to, and nothing '
            "registers it, so every DObject lookup fails. Register it in the "
            f'deps.py of an app with @resolver.dependency("{SCOPE_DEPENDENCY}"), '
            "returning the user, the tenant, or whatever visible_to accepts.",
            id="next.E159",
        )
    ]


@register(NEXT)
def check_object_hints(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `DObject` whose callable's type hints do not resolve (`next.E160`).

    The resolver refuses such a parameter on every call, so the check names it first.
    """
    sites = object_sites()
    if sites is None:
        return []
    return [
        Error(
            f'Parameter "{name}" of {describe_callable(func)} names DObject, but '
            "the type hints of that callable do not resolve, so every resolve "
            f"raises TypeError. {HINT_ADVICE}",
            obj=describe_callable(func),
            id="next.E160",
        )
        for func in _all_callables(sites)
        for name in _unresolved_params(func)
    ]


__all__ = [
    "check_object_component_contexts",
    "check_object_form_parents",
    "check_object_hints",
    "check_object_managers",
    "check_object_page_bindings",
    "check_object_scope",
]
