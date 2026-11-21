"""The checks of client-trip component calls and of components that declare props."""

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError
from django.http import HttpRequest
from django.template import Engine, Template, TemplateSyntaxError
from django.template.base import Node, NodeList, TextNode
from django.template.defaulttags import IfNode

from next.components.context import component
from next.components.deferral import defer_declarations
from next.components.info import ComponentInfo
from next.components.reads import reads
from next.components.sources import get_components_manager
from next.components.trip import addressable, client_mode, declared_props, prop_types
from next.conf import next_framework_settings
from next.defer import DeferMode
from next.pages.checks.composed import iter_composed_pages
from next.pages.processors import context_processors
from next.permissions.access import declared
from next.permissions.nodes import HasPermNode
from next.suspense.errors import BoundaryPropsError
from next.suspense.tokens.props import check_annotation, encode_props
from next.templatetags.components import ComponentNode
from next.utils import child_nodelists


_GIVEN: Final = frozenset(
    {"request", "csrf_token", "children", "True", "False", "None"}
)
_GIVEN_PREFIXES: Final = ("_", "slot_", "current_")
_PROCESSOR_FAILURES: Final = (
    AttributeError,
    DatabaseError,
    ImproperlyConfigured,
    LookupError,
    TypeError,
    ValueError,
)


@dataclass(frozen=True, slots=True)
class Call:
    """One component call of a template, the component it resolves to and its mode."""

    node: ComponentNode
    info: ComponentInfo
    mode: DeferMode | None
    where: Path
    guarded: bool


def _walk(
    nodelist: NodeList, ancestors: tuple[Node, ...] = ()
) -> Iterator[tuple[Node, tuple[Node, ...]]]:
    for node in nodelist:
        yield node, ancestors
        for child in child_nodelists(node):
            yield from _walk(child, (*ancestors, node))


def _ruled(info: ComponentInfo | None) -> bool:
    """Whether `info` declares an access rule of its own."""
    if info is None or info.module_path is None:
        return False
    return bool(declared(info.module_path).requirements)


def _mode(node: ComponentNode, info: ComponentInfo) -> DeferMode | None:
    """Return the mode a call runs under, the call site over the module."""
    written = node.options
    if written is not None and written.mode is not None:
        return written.mode
    options = defer_declarations.options(info.module_path)
    return None if options is None else options.mode


def _rechecked(node: HasPermNode, call: ComponentNode) -> bool:
    """Whether a reload checks `node` again, a literal model permission over `call`."""
    granted = node.nodelist_true.get_nodes_by_type(ComponentNode)
    return (
        node.obj is None
        and node.literal_perm() is not None
        and any(held is call for held in granted)
    )


def _hides(node: Node, call: ComponentNode, anchor: Path) -> bool:
    """Whether `node` decides what the visitor sees of `call` where no reload can."""
    if isinstance(node, IfNode):
        return True
    if isinstance(node, HasPermNode):
        return not _rechecked(node, call)
    if isinstance(node, ComponentNode):
        return _ruled(get_components_manager().get_component(node.name, anchor))
    return False


def _calls(template: Template, anchor: Path) -> Iterator[Call]:
    """Yield every component call of `template` that resolves from `anchor`."""
    for node, ancestors in _walk(template.nodelist):
        if not isinstance(node, ComponentNode):
            continue
        info = get_components_manager().get_component(node.name, anchor)
        if info is None:
            continue
        guarded = any(_hides(ancestor, node, anchor) for ancestor in ancestors)
        yield Call(node, info, _mode(node, info), anchor, guarded)


def _components() -> Iterator[ComponentInfo]:
    """Yield every component of the listed backends, each `component.py` imported."""
    if not next_framework_settings.COMPONENT_BACKENDS:
        return
    for backend in get_components_manager().backends:
        backend.import_component_modules()
        yield from backend.iter_components()


def deferred_components() -> Iterator[ComponentInfo]:
    """Yield every component that defers, by `component.defer` or a `loading.djx`."""
    for info in _components():
        files = info.files
        options = defer_declarations.options(info.module_path)
        if (files is not None and files.loading is not None) or (
            options is not None and options.mode is not None
        ):
            yield info


def _component_templates() -> Iterator[tuple[Path, Template]]:
    """Yield the compiled template of every component, skipping one that fails."""
    for info in tuple(_components()):
        path = info.template_path
        if path is None:
            continue
        try:
            yield path, Template(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, TemplateSyntaxError):
            continue


def client_calls() -> Iterator[Call]:
    """Yield every client-trip call of the page templates and component templates."""
    sources = (*_component_templates(), *iter_composed_pages())
    for anchor, template in sources:
        for call in _calls(template, anchor):
            if client_mode(call.mode):
                yield call


def _opening(site: str) -> str:
    """Return the site of a call as the start of a sentence."""
    return site[:1].upper() + site[1:]


def _significant(node: Node) -> bool:
    return not isinstance(node, TextNode) or bool(node.s.strip())


def _shape_error(call: Call) -> CheckMessage | None:
    """Return `next.E224` for a call with undeclared props, children or slots."""
    node = call.node
    undeclared = sorted(node.prop_names - set(declared_props(call.info)))
    if undeclared:
        problem = f"passes the undeclared props {', '.join(undeclared)}"
    elif node.has_slots or any(map(_significant, node.nodelist)):
        problem = "gives it children or slots"
    else:
        return None
    return Error(
        f"{_opening(node.site)} {problem}, but the component loads on a later "
        "request from its declared props alone.",
        hint="Declare every prop with component.defer(mode, props=(...)) and pass no "
        "children or slots to a client-trip component.",
        obj=str(call.where),
        id="next.E224",
    )


def _unnamed_object(info: ComponentInfo) -> str | None:
    """Return the `obj=` of the rule of `info` that names no declared prop."""
    if info.module_path is None:
        return None
    props = declared_props(info)
    for requirement in declared(info.module_path).requirements:
        obj = requirement.obj
        if obj is not None and (not isinstance(obj, str) or obj not in props):
            return obj if isinstance(obj, str) else obj._meta.label
    return None


def _object_error(info: ComponentInfo) -> CheckMessage | None:
    """Return `next.E225` for a client-trip rule whose object is no declared prop."""
    obj = _unnamed_object(info)
    if obj is None:
        return None
    return Error(
        f"Component {info.name!r} loads on a later request, and its access rule names "
        f"the object {obj!r}, which is no declared prop, so the shell cannot check it "
        "before it mints the token.",
        hint="Name a declared prop in permission_required(perm, obj=...), or declare "
        "the object as a prop.",
        obj=str(info.module_path),
        id="next.E225",
    )


def _unguarded(call: Call) -> CheckMessage | None:
    """Return `next.W159` for a guarded call of a component without a rule."""
    if not call.guarded or _ruled(call.info):
        return None
    return DjangoWarning(
        f"{_opening(call.node.site)} sits under an {{% if %}}, an object-scoped, "
        "variable or else-branch {% has_perm %} or a guarding component, which a "
        f"reload does not check again, and {call.info.name!r} declares no access rule.",
        hint="Declare the access the page implies in its component.py, with "
        "component.login_required() or component.permission_required().",
        obj=str(call.where),
        id="next.W159",
    )


def _declared_client(info: ComponentInfo) -> bool:
    options = defer_declarations.options(info.module_path)
    return options is not None and client_mode(options.mode)


def mints_tokens() -> bool:
    """Whether a component declares a client-trip mode or props, or a call loads one."""
    if any(_declared_client(info) or declared_props(info) for info in _components()):
        return True
    return next(client_calls(), None) is not None


def check_client_calls(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report the calls the reload path cannot serve (`E224`, `E225`, `W159`)."""
    messages: dict[tuple[str | None, str], CheckMessage] = {}
    infos: dict[tuple[str, Path | None], ComponentInfo] = {}
    for call in client_calls():
        infos[call.info.name, call.info.module_path] = call.info
        for message in (_shape_error(call), _unguarded(call)):
            if message is not None:
                messages[message.id, message.msg] = message
    for info in _components():
        if _declared_client(info):
            infos[info.name, info.module_path] = info
    for info in infos.values():
        message = _object_error(info)
        if message is not None:
            messages[message.id, message.msg] = message
    return list(messages.values())


def _token_calls() -> Iterator[Call]:
    """Yield every call of a component a token renders, by its mode or its props."""
    sources = (*_component_templates(), *iter_composed_pages())
    for anchor, template in sources:
        for call in _calls(template, anchor):
            if client_mode(call.mode) or addressable(call.info):
                yield call


def _annotation_errors(info: ComponentInfo) -> Iterator[CheckMessage]:
    """Yield `next.E243` for each declared prop annotated with a type no token holds."""
    for name, annotation in prop_types(info).items():
        try:
            check_annotation(name, annotation)
        except BoundaryPropsError as exc:
            yield Error(
                f"Component {info.name!r} cannot mint its token, as {exc}",
                hint="Annotate the prop with a type the token codec carries.",
                obj=str(info.module_path),
                id="next.E243",
            )


def _constant_error(call: Call) -> CheckMessage | None:
    """Return `next.E243` for a constant prop of `call` that its token refuses."""
    names = frozenset(declared_props(call.info))
    constants = {
        name: value
        for name, value in call.node.constant_props().items()
        if name in names
    }
    try:
        encode_props(constants, prop_types(call.info), limit=sys.maxsize)
    except BoundaryPropsError as exc:
        return Error(
            f"{_opening(call.node.site)} cannot mint its token, as {exc}",
            hint="Pass a value of the type the prop is annotated with.",
            obj=str(call.where),
            id="next.E243",
        )
    return None


def check_prop_types(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Refuse a declared prop no boundary token can carry (`next.E243`).

    Its annotation is read once per component, a constant once per call site.
    """
    messages: dict[tuple[str | None, str], CheckMessage] = {}
    refused: set[Path | None] = set()
    for info in tuple(_components()):
        for message in _annotation_errors(info):
            messages[message.id, message.msg] = message
            refused.add(info.module_path)
    for call in _token_calls():
        if call.info.module_path in refused:
            continue
        found = _constant_error(call)
        if found is not None:
            messages[found.id, found.msg] = found
    return list(messages.values())


def processor_names() -> frozenset[str] | None:
    """Return every name the context processors give, `None` when one cannot say."""
    names: set[str] = set()
    processors = (
        *Engine.get_default().template_context_processors,
        *context_processors(),
    )
    for processor in dict.fromkeys(processors):
        try:
            given = processor(HttpRequest())
        except _PROCESSOR_FAILURES:
            return None
        names.update(given)
    return frozenset(names)


def _context_keys(module_path: Path) -> frozenset[str] | None:
    """Return the keys the context functions of a module give, `None` for keyless."""
    keys: set[str] = set()
    for entry in component.get_functions(module_path):
        if entry.key is None:
            return None
        keys.add(entry.key)
    return frozenset(keys)


def _given(name: str) -> bool:
    return name in _GIVEN or name.startswith(_GIVEN_PREFIXES)


def _stray(info: ComponentInfo, processors: frozenset[str]) -> list[str]:
    """Return the variables the template of `info` reads that nothing gives it."""
    path, module = info.template_path, info.module_path
    if path is None or module is None:
        return []
    keys = _context_keys(module)
    if keys is None:
        return []
    try:
        template = Template(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, TemplateSyntaxError):
        return []
    read, assigned = reads(template.nodelist)
    known = keys | processors | assigned | set(declared_props(info))
    return sorted(name for name in read - known if not _given(name))


def check_declared_props(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a variable a props-declaring component reads but is never given.

    A reload renders such a component from its declared props alone (`next.W156`).
    """
    processors = processor_names()
    if processors is None:
        return []
    return [
        DjangoWarning(
            f"Component {info.name!r} declares its props, and its template reads "
            f"{', '.join(stray)}, which no declared prop, context function or context "
            "processor gives it, so a reload renders it empty.",
            hint="Declare it as a prop, compute it in a @component.context function, "
            "or bind it in the template.",
            obj=str(info.template_path),
            id="next.W156",
        )
        for info in tuple(_components())
        if declared_props(info) and (stray := _stray(info, processors))
    ]


__all__ = [
    "Call",
    "check_client_calls",
    "check_declared_props",
    "check_prop_types",
    "client_calls",
    "deferred_components",
    "mints_tokens",
    "processor_names",
]
