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
from next.components.sources import components_listed, import_component_modules
from next.forms.checks.sources import iter_registered_actions
from next.introspect import describe_callable
from next.pages.access import page_access, subtree_only
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.manager import page
from next.urls.routes import page_route

from .introspect import HINT_ERRORS, cached_signature, cached_type_hints
from .objects import (
    HINT_ADVICE,
    SCOPE_DEPENDENCY,
    object_kwarg,
    object_target,
    scoped_query,
    unresolved_object,
    unscoped_reason,
)
from .resolver import current_resolver


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    from django.db.models import Model

    from next.pages.registry import PageContextEntry
    from next.permissions.markers import Requirement
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


class _ObjectDeclaration(NamedTuple):
    """One `permission_required(obj=DObject[...])` a page answers to."""

    requirement: Requirement
    model: type[Model]


class _PageRun(NamedTuple):
    """A routed `page.py`, the kwargs its full route hands it, and its context order.

    A refused route has no kwargs, and `metadata` runs after `steps`, ancestors first.
    """

    route: str
    page_path: Path
    route_kwargs: frozenset[str] | None
    steps: tuple[tuple[str | None, PageContextEntry], ...]
    metadata: tuple[Callable[..., Any], ...]
    objects: tuple[_ObjectDeclaration, ...]


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


def _metadata_callables(page_path: Path) -> tuple[Callable[..., Any], ...]:
    """Return the `@page.metadata` callables a render of `page_path` folds, root first.

    A refused chain runs none of them, and `next.E102` and its neighbours report it.
    """
    try:
        chain = page.metadata_chain(page_path)
    except (PageMetadataShapeError, PageMetadataConflictError):
        return ()
    return tuple(source.func for source in chain.sources if source.func is not None)


def _object_declarations(page_path: Path) -> tuple[_ObjectDeclaration, ...]:
    """Return the `obj=DObject[...]` requirements the rule of `page_path` holds."""
    return tuple(
        _ObjectDeclaration(requirement, requirement.obj)
        for requirement in page_access(page_path).rule.requirements
        if isinstance(requirement.obj, type)
    )


def _page_run(root: PageRoot, url_path: str, page_path: Path) -> _PageRun:
    """Return the run order of every context callable a render of `page_path` calls."""
    steps = page.contexts.run_order(page_path)
    metadata = _metadata_callables(page_path)
    objects = _object_declarations(page_path)
    route = page_route(root, url_path)
    if route is None:
        return _PageRun(url_path, page_path, None, steps, metadata, objects)
    return _PageRun(route.url, page_path, route.kwargs, steps, metadata, objects)


def _loaded_pages(
    router_manager: RouterManager,
) -> Iterator[tuple[PageRoot, str, Path]]:
    """Yield each page that loaded once, with the tree the walk found it in.

    A `page.py` that only guards the pages below serves no route, so it is left out.
    """
    loaded = {path for _url_path, path in discover_page_registrations(router_manager)}
    for router in router_manager.backends:
        for root, url_path, page_path in iter_scanned_pages(router):
            if page_path in loaded and not subtree_only(page_path):
                loaded.discard(page_path)
                yield root, url_path, page_path


def _component_callables() -> Iterator[Callable[..., Any]]:
    """Yield every `@component.context` callable once the component modules ran."""
    if not components_listed():
        return
    import_component_modules()
    for functions in component.registry.registered().values():
        for function in functions:
            yield function.func


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
    metadata_funcs = (func for run in sites.pages for func in run.metadata)
    seen: set[int] = set()
    for func in (
        *page_funcs,
        *metadata_funcs,
        *sites.components,
        *sites.forms,
        *sites.dependencies,
    ):
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


def _declared_site(declaration: _ObjectDeclaration) -> str:
    """Spell an `obj=` declaration and the file making it for a message."""
    requirement = declaration.requirement
    perms = ", ".join(requirement.perms)
    label = declaration.model._meta.label
    return (
        f"permission_required({perms!r}, obj=DObject[{label}]) in {requirement.source}"
    )


def _declarations(sites: _ObjectSites) -> Iterator[_ObjectDeclaration]:
    """Yield every `obj=DObject[...]` some page answers to, each once."""
    seen: set[_ObjectDeclaration] = set()
    for run in sites.pages:
        for declaration in run.objects:
            if declaration not in seen:
                seen.add(declaration)
                yield declaration


def _model_sites(sites: _ObjectSites) -> Iterator[tuple[type[Model], str]]:
    """Yield the model and the spelled site of every row a `DObject` loads."""
    for param in _all_params(sites):
        yield param.model, f"{_marker(param)} on {_site(param)}"
    for declaration in _declarations(sites):
        yield declaration.model, _declared_site(declaration)


@register(NEXT)
def check_object_managers(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `DObject` model whose manager defines no `visible_to` (`next.E153`)."""
    sites = object_sites()
    if sites is None:
        return []
    errors: list[CheckMessage] = []
    checked: set[type[Model]] = set()
    for model, site in _model_sites(sites):
        if model in checked:
            continue
        checked.add(model)
        if scoped_query(model) is not None:
            continue
        errors.append(
            Error(
                f"{site} loads its row through {unscoped_reason(model)}",
                obj=model,
                id="next.E153",
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
        id="next.W126",
    )


def _parent_error(run: _PageRun, param: _ObjectParam) -> Error:
    """Report a page context `DObject` whose parent no earlier context provides."""
    return Error(
        f"{_marker(param)} on {_site(param)} filters by the context value "
        f'"{param.parent}", which no context callable running before it on the '
        f'"{run.route}" route provides. Declare it with '
        f'@context("{param.parent}", inherit_context=True) in an ancestor page.py.',
        obj=str(run.page_path),
        id="next.E154",
    )


def _binding_messages(
    run: _PageRun,
    func: Callable[..., Any],
    provided: dict[str, frozenset[str] | None] | None,
    zones: frozenset[str] | None,
) -> Iterator[CheckMessage]:
    """Yield the route and parent messages of the `DObject` parameters of `func`.

    No `provided` mapping means a keyless callable ran earlier, so no parent is judged.
    """
    for param in _object_params(func):
        if run.route_kwargs is not None and param.name not in run.route_kwargs:
            yield _route_warning(run, param)
        parent = param.parent
        if parent is None or provided is None:
            continue
        if not _provides(provided, parent, zones):
            yield _parent_error(run, param)


def _page_run_messages(run: _PageRun) -> Iterator[CheckMessage]:
    """Yield the binding messages of every `DObject` on one page, in run order.

    A parent after a keyless callable is unjudged, a metadata one holds on every render.
    """
    provided: dict[str, frozenset[str] | None] = {}
    open_keys = False
    for key, entry in run.steps:
        yield from _binding_messages(
            run, entry.func, None if open_keys else provided, entry.zones
        )
        if key is None:
            open_keys = True
        else:
            provided.setdefault(key, entry.zones)
    for func in run.metadata:
        yield from _binding_messages(run, func, None if open_keys else provided, None)


@register(NEXT)
def check_object_page_bindings(*args, **kwargs) -> list[CheckMessage]:
    """Flag a page `DObject` with no route kwarg (`next.W126`) or parent (`next.E154`).

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
    """Flag a parent `DObject` on a form handler or hook (`next.E155`).

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
            id="next.E155",
        )
        for param in _params_of(sites.forms)
        if param.parent is not None
    )


@register(NEXT)
def check_object_component_contexts(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `DObject` on a `@component.context` callable (`next.E156`).

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
            id="next.E156",
        )
        for param in _params_of(sites.components)
    )


@register(NEXT)
def check_object_scope(*args, **kwargs) -> list[CheckMessage]:
    """Flag `DObject` parameters with no `scope` dependency registered (`next.E157`).

    A project that declares no `DObject` needs no scope, so the check stays silent.
    """
    sites = object_sites()
    if sites is None:
        return []
    if current_resolver().get_dependency(SCOPE_DEPENDENCY) is not None:
        return []
    first = next(_model_sites(sites), None)
    if first is None:
        return []
    return [
        Error(
            f'{first[1]} passes the "{SCOPE_DEPENDENCY}" dependency to '
            "visible_to, and nothing registers it, so every DObject lookup fails. "
            "Register it in the deps.py of an app with "
            f'@resolver.dependency("{SCOPE_DEPENDENCY}"), returning the user, the '
            "tenant, or whatever visible_to accepts.",
            id="next.E157",
        )
    ]


def _unbound_declaration(run: _PageRun, declaration: _ObjectDeclaration) -> Error:
    """Report an `obj=` whose model names a kwarg the route of a page lacks."""
    kwarg = object_kwarg(declaration.model)
    captured = ", ".join(sorted(run.route_kwargs or ())) or "none"
    return Error(
        f'{_declared_site(declaration)} loads its row by the URL kwarg "{kwarg}", '
        f'but the route "{run.route}" of {run.page_path} under it captures '
        f"{captured}, so every request to that page raises UnboundObjectError.",
        hint=(
            f"Name the segment [{kwarg}] or [int:{kwarg}], or capture {kwarg} in "
            "the URLconf include over the tree."
        ),
        obj=str(declaration.requirement.source),
        id="next.E162",
    )


@register(NEXT)
def check_object_declarations(*args, **kwargs) -> list[CheckMessage]:
    """Flag an `obj=DObject[...]` whose kwarg a page under it lacks (`next.E162`).

    The kwarg is the `model_name` of the model, read like a `DObject` parameter's name.
    """
    sites = object_sites()
    if sites is None:
        return []
    return _unique(
        _unbound_declaration(run, declaration)
        for run in sites.pages
        if run.route_kwargs is not None
        for declaration in run.objects
        if object_kwarg(declaration.model) not in run.route_kwargs
    )


@register(NEXT)
def check_object_hints(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `DObject` whose callable's type hints do not resolve (`next.E158`).

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
            id="next.E158",
        )
        for func in _all_callables(sites)
        for name in _unresolved_params(func)
    ]


__all__ = [
    "check_object_component_contexts",
    "check_object_declarations",
    "check_object_form_parents",
    "check_object_hints",
    "check_object_managers",
    "check_object_page_bindings",
    "check_object_scope",
]
