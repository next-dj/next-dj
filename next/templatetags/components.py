"""Template tags for next-dj components (void/block ``{% component %}``, slots).

Props and slot HTML resolve from ``current_template_path`` and pass to the renderer.
"""

from __future__ import annotations

import difflib
import functools
import itertools
import logging
import re
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Self, cast, override
from weakref import WeakKeyDictionary

from django import template
from django.conf import settings
from django.template.base import (
    FilterExpression,
    Node,
    NodeList,
    Parser,
    Template,
    TextNode,
    Token,
    Variable,
    VariableNode,
    token_kwargs,
)
from django.template.defaulttags import ForNode
from django.template.loader_tags import BlockNode, IncludeNode
from django.templatetags.cache import CacheNode
from django.utils.safestring import SafeString

from next.boundaries import (
    Boundary,
    claim_wrapper,
    contain,
    misrooted,
    new_digest,
    wrapper_tag,
)
from next.components import collect_visible_components, get_component, render_component
from next.components.boundary import (
    ADDRESSING,
    UNDISCLOSED,
    Attempt,
    current_attempt,
    deferral_sink,
    ruled,
)
from next.components.deferral import defer_declarations
from next.components.facade import render_component_error, render_component_loading
from next.components.fallback import retry_loads, unrun_error
from next.components.nodes import ComponentTagNode, SetSlotTagNode, SlotTagNode
from next.components.registry import deferred_components, may_defer
from next.components.renderers import COMPONENT_PROPS_CONTEXT_KEY, SLOT_KEY_PREFIX
from next.components.stash import in_loading, loading_content, stash_loading
from next.components.trip import TripInput, addressable, client_mode, declared_props
from next.conf import fail_loudly, next_framework_settings
from next.defer import (
    Addressing,
    BoundaryError,
    BoundaryOptionError,
    BoundaryOptions,
    DeferMode,
    DeferredCall,
    ErrorKind,
    Site,
    stable_key,
)
from next.diagnostics import Capture, mark_degraded
from next.interpolation import Interpolation, interpolated
from next.pages.boundaries import shown_source
from next.permissions.access import check, declared
from next.permissions.nodes import (
    claimed,
    granted_perms,
    rendered_under,
    rendering_guards,
)
from next.seeding import COLLECTOR_KEY, REQUEST_KEY, TEMPLATE_PATH_KEY
from next.static import StaticCollector, collect_component_assets
from next.utils import (
    app_holding,
    child_nodelists,
    on_forget_resolved_trees,
    resolve_base_dir,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from next.components.info import ComponentInfo
    from next.permissions.nodes import Guards


logger = logging.getLogger(__name__)

register = template.Library()

_SLOT_ARG_COUNT = 2

# Distinct ``current_template_path`` strings memoised, matching the other path caches.
_PATH_MEMO_MAX_ENTRIES = 2048

_END_BLOCK_COMPONENT = ("/component",)
_END_BLOCK_SLOT = ("/slot",)
_END_BLOCK_SET_SLOT = ("/set_slot",)

# The key a component pushes for its body to write slots into.
_SLOT_COLLECTOR_KEY = "_component_slots"

# Body-only keys. ``_component_props`` is not listed as every node overwrites it.
_INTERNAL_CONTEXT_KEYS = frozenset({_SLOT_COLLECTOR_KEY})

_DASH_BEFORE_DASH = re.compile(r"-(?=-)")

# Distinguishes an absent slot key from an empty one, only absence takes the default.
_SLOT_MISSING: Any = object()

_WRAPPER = "div"

_OPTION_KEYS: Final = ("defer", "timeout", "reload", "delay")
_NOT_LITERAL: Final = object()

_START_TAG: Final = re.compile(
    r"<(?P<close>/?)(?P<name>[A-Za-z][^\s/>]*)"
    r"(?P<attrs>(?:[^>\"']|\"[^\"]*\"|'[^']*')*)>"
)
_KEYED: Final = re.compile(
    r"""(?:^|\s)data-next-key\s*=\s*(?:"(?P<double>[^"]*)"|'(?P<single>[^']*)')"""
)
_HOLE: Final = re.compile("\x00([0-9]+)\x00")
_CAPTURING_TAGS: Final = frozenset({"cache"})
_ORDINAL: Final = "next.component.ordinal"
_MISROOTED: Final = (
    "%s renders %s where its boundary adopts the root element, so it settles as an "
    "error."
)
_MISROOTED_LOG: Final = _MISROOTED[:-1] + ", digest %s."

_sites: WeakKeyDictionary[Parser, Iterator[int]] = WeakKeyDictionary()
_captures: WeakKeyDictionary[Template, tuple[IncludeNode, ...]] = WeakKeyDictionary()
_contents: WeakKeyDictionary[
    Template, tuple[frozenset[int], tuple[IncludeNode, ...]]
] = WeakKeyDictionary()

_capturing: ContextVar[bool] = ContextVar("next_component_capturing", default=False)

type _Piece = str | FilterExpression


def _strip_quotes(raw: str) -> str:
    return raw.strip("'\"").strip()


def _site_index(parser: Parser) -> int:
    """Return the position of the next component call among those of one template."""
    counter = _sites.get(parser)
    if counter is None:
        counter = _sites[parser] = itertools.count()
    return next(counter)


def _literal(expr: FilterExpression | Interpolation) -> object:
    """Return the literal `expr` writes, `_NOT_LITERAL` when it reads the context."""
    if isinstance(expr, Interpolation) or expr.filters:
        return _NOT_LITERAL
    var = expr.var
    if isinstance(var, Variable):
        return _NOT_LITERAL if var.literal is None else var.literal
    return str.__str__(var)


_CONSTANTS: Final[dict[tuple[str, ...], object]] = {
    ("True",): True,
    ("False",): False,
    ("None",): None,
}


def _constant(expr: FilterExpression | Interpolation) -> object:
    """Return the value `expr` renders in any context, `_NOT_LITERAL` for no constant.

    `True`, `False` and `None` count, as every template context holds them.
    """
    if isinstance(expr, FilterExpression) and not expr.filters:
        var = expr.var
        if isinstance(var, Variable) and var.literal is None:
            return _CONSTANTS.get(var.lookups or (), _NOT_LITERAL)
    return _literal(expr)


def _parse_options(
    tag: str, props: dict[str, FilterExpression | Interpolation]
) -> BoundaryOptions | None:
    """Take the boundary options out of `props`, `None` when the call writes none.

    `reload=` and `delay=` are reserved and checked as durations, with no effect yet.
    """
    written = {key: props.pop(key) for key in _OPTION_KEYS if key in props}
    if not written:
        return None
    values: dict[str, object] = {}
    for key, expr in written.items():
        value = _literal(expr)
        if value is _NOT_LITERAL:
            msg = f"{tag} tag takes {key}= as a literal, not a template variable"
            raise template.TemplateSyntaxError(msg)
        values[key] = value
    try:
        options = BoundaryOptions.parse(
            values.get("defer"), timeout=values.get("timeout")
        ).paced(reload=values.get("reload"), delay=values.get("delay"))
    except BoundaryOptionError as error:
        msg = f"{tag} tag refuses its boundary options, {error}"
        raise template.TemplateSyntaxError(msg) from error
    if options.mode is not None:
        may_defer()
    return options


@functools.lru_cache(maxsize=_PATH_MEMO_MAX_ENTRIES)
def _resolve_template_path(raw: str) -> Path:
    """Return the resolved file for a raw ``current_template_path`` string.

    Memoised per raw string and dropped along with the shared page-tree resolutions.
    """
    return Path(raw).resolve()


on_forget_resolved_trees(_resolve_template_path.cache_clear)


def _setting_roots() -> tuple[Path, ...]:
    """Return the `DIRS` roots of the page and component backends, resolved."""
    roots: list[Path] = []
    for entry in (
        *next_framework_settings.PAGE_BACKENDS,
        *next_framework_settings.COMPONENT_BACKENDS,
    ):
        dirs = entry.get("DIRS") if isinstance(entry, dict) else None
        if isinstance(dirs, list | tuple):
            roots.extend(
                Path(raw).resolve() for raw in dirs if isinstance(raw, str | Path)
            )
    return tuple(roots)


@functools.lru_cache(maxsize=_PATH_MEMO_MAX_ENTRIES)
def _anchored(name: str) -> str:
    """Return the template file `name` relative to the app or the root holding it.

    A key hashes it, so it stays the same across hosts and release directories.
    """
    path = Path(name)
    if not path.is_absolute():
        return name
    held = app_holding(path)
    if held is not None:
        return f"{held[0]}:{held[1]}"
    real = path.resolve()
    for position, root in enumerate(_setting_roots()):
        if real.is_relative_to(root):
            return f"{position}:{real.relative_to(root).as_posix()}"
    base = resolve_base_dir()
    if base is not None and real.is_relative_to(base.resolve()):
        return real.relative_to(base.resolve()).as_posix()
    return name


on_forget_resolved_trees(_anchored.cache_clear)


def _captured_includes(template: Template) -> tuple[IncludeNode, ...]:
    """Return the `{% include %}` nodes `template` holds inside a `{% cache %}` body."""
    held = _captures.get(template)
    if held is None:
        caches = cast("list[CacheNode]", template.nodelist.get_nodes_by_type(CacheNode))
        held = _captures[template] = tuple(
            include
            for cache in caches
            for include in cast(
                "list[IncludeNode]", cache.nodelist.get_nodes_by_type(IncludeNode)
            )
        )
    return held


def _content(template: Template) -> tuple[frozenset[int], tuple[IncludeNode, ...]]:
    """Return the component calls and the includes `template` holds, at any depth."""
    held = _contents.get(template)
    if held is None:
        nodelist = template.nodelist
        held = _contents[template] = (
            frozenset(map(id, nodelist.get_nodes_by_type(ComponentTagNode))),
            tuple(cast("list[IncludeNode]", nodelist.get_nodes_by_type(IncludeNode))),
        )
    return held


def _included(
    node: Node, includes: tuple[IncludeNode, ...], seen: dict[Any, Any]
) -> bool:
    """Whether the templates `includes` rendered in this render hold `node`.

    Django keeps the template each include rendered in the bottom render context.
    """
    pending = list(includes)
    visited: set[int] = set()
    while pending:
        for held in seen.get(pending.pop(), {}).values():
            if id(held) in visited:
                continue
            visited.add(id(held))
            calls, nested = _content(held)
            if id(node) in calls:
                return True
            pending.extend(nested)
    return False


def _comment_safe(text: str) -> str:
    """Break every `--` run so `text` cannot terminate an HTML comment early.

    An odd run would still close it, so a space follows each dash before a dash.
    """
    return _DASH_BEFORE_DASH.sub("- ", text)


@dataclass(frozen=True, slots=True)
class _Call:
    """What one component tag compiles to before its body is parsed."""

    name: str
    props: dict[str, FilterExpression | Interpolation]
    options: BoundaryOptions | None
    index: int
    captured: bool
    perms: tuple[str, ...] | None = ()


def _parse_component(parser: Parser, token: Token, tag: str) -> _Call:
    """Parse ``tag "name" key=expr …`` into the component name, props and options.

    Django reads the props, so each value compiles to a render-time `FilterExpression`.
    """
    bits = token.split_contents()[1:]
    if not bits:
        msg = f"{tag} tag requires at least a component name"
        raise template.TemplateSyntaxError(msg)
    name = _strip_quotes(bits.pop(0))
    if not name:
        msg = f"{tag} tag requires a quoted component name"
        raise template.TemplateSyntaxError(msg)
    props = _parse_props(parser, bits)
    options = _parse_options(tag, props)
    stack = parser.command_stack
    captured = any(command in _CAPTURING_TAGS for command, _ in stack)
    perms = granted_perms(parser)
    return _Call(name, props, options, _site_index(parser), captured, perms)


def _parse_props(
    parser: Parser, bits: list[str]
) -> dict[str, FilterExpression | Interpolation]:
    """Parse the `key=expr` props a component tag carries, consuming `bits`."""
    props: dict[str, FilterExpression | Interpolation] = {}
    while bits:
        for key, expr in token_kwargs(bits, parser).items():
            props[key] = interpolated(parser, expr) or expr
        if bits:
            # Django stops at the first non-pair bit, an unmeant word, so it is dropped.
            del bits[0]
    return props


@dataclass(frozen=True, slots=True)
class _NamedTagSpec:
    """What one named slot tag needs to compile, in its paired or its short form."""

    empty_name_message: str
    wrong_arity_message: str
    end_tokens: tuple[str, ...] = ()


def _parse_named_tag(
    parser: Parser, token: Token, spec: _NamedTagSpec
) -> tuple[str, NodeList]:
    """Parse ``tag "name"`` into a name and the body only the paired form carries."""
    bits = token.split_contents()
    if len(bits) != _SLOT_ARG_COUNT:
        raise template.TemplateSyntaxError(spec.wrong_arity_message)
    name = _strip_quotes(bits[1])
    if not name:
        raise template.TemplateSyntaxError(spec.empty_name_message)
    if not spec.end_tokens:
        return name, NodeList()
    nodelist = parser.parse(spec.end_tokens)
    parser.delete_first_token()
    return name, nodelist


def _text_before(
    nodelist: NodeList, target: Callable[[Node], bool], parts: list[str]
) -> bool:
    """Gather the template text before the node `target` picks, True once found."""
    for node in nodelist:
        if target(node):
            return True
        if isinstance(node, TextNode):
            parts.append(node.s)
            continue
        if any(_text_before(child, target, parts) for child in child_nodelists(node)):
            return True
    return False


@dataclass(slots=True)
class _Walk:
    """The template pieces before a call, from the innermost loop body around it."""

    pieces: list[_Piece] = field(default_factory=list)
    looped: bool = False


def _pieces_before(
    nodelist: NodeList, target: Callable[[Node], bool], walk: _Walk
) -> bool:
    """Gather the text and variables before the node `target` picks, True once found."""
    for node in nodelist:
        if target(node):
            return True
        if isinstance(node, TextNode):
            walk.pieces.append(node.s)
        elif isinstance(node, VariableNode):
            walk.pieces.append(node.filter_expression)
        elif isinstance(node, ForNode):
            inner = _Walk()
            loop = node.nodelist_loop
            if isinstance(loop, NodeList) and _pieces_before(loop, target, inner):
                walk.pieces = inner.pieces
                walk.looped = True
                return True
        elif any(
            _pieces_before(child, target, walk) for child in child_nodelists(node)
        ):
            return True
    return False


def _keyed_row(pieces: list[_Piece]) -> tuple[_Piece, ...] | None:
    """Return the `data-next-key` value of the innermost open element, as pieces."""
    holes: list[FilterExpression] = []
    text: list[str] = []
    for piece in pieces:
        if isinstance(piece, str):
            text.append(piece)
        else:
            text.append(f"\x00{len(holes)}\x00")
            holes.append(piece)
    stack: list[tuple[str, str]] = []
    for match in _START_TAG.finditer("".join(text)):
        name = match["name"].lower()
        if not match["close"]:
            stack.append((name, match["attrs"]))
            continue
        for depth in range(len(stack) - 1, -1, -1):
            if stack[depth][0] == name:
                del stack[depth:]
                break
    for _name, attrs in reversed(stack):
        keyed = _KEYED.search(attrs)
        if keyed is not None:
            value = keyed["double"] if keyed["double"] is not None else keyed["single"]
            return tuple(
                holes[int(part)] if position % 2 else part
                for position, part in enumerate(_HOLE.split(value))
                if position % 2 or part
            )
    return None


def _finds(nodelist: NodeList, target: Callable[[Node], bool]) -> bool:
    """Whether `nodelist` holds the node `target` picks, at any depth."""
    return any(
        target(node) or any(_finds(child, target) for child in child_nodelists(node))
        for node in nodelist
    )


def _blocks(context: template.Context) -> tuple[BlockNode, ...]:
    """Return the blocks rendering around the current node, the outermost first."""
    return tuple(
        block
        for layer in context.dicts
        if isinstance(block := layer.get("block"), BlockNode)
    )


def _nodelists(blocks: tuple[BlockNode, ...]) -> tuple[NodeList, ...]:
    """Return the nodelists of `blocks`, which outlive the copies Django renders."""
    return tuple(block.nodelist for block in blocks)


@dataclass(frozen=True, slots=True)
class _Placement:
    """What the static markup before one call decides, worked out once per site."""

    tag: str | None
    recipe: tuple[_Piece, ...] | None

    @classmethod
    def of(cls, path: tuple[tuple[NodeList, Callable[[Node], bool]], ...]) -> Self:
        """Read the markup before each step of `path`, a nodelist and the node it holds.

        The text decides the wrapper, the pieces of the innermost loop body the row key.
        """
        parts: list[str] = []
        walk = _Walk()
        for nodelist, target in path:
            _text_before(nodelist, target, parts)
            _pieces_before(nodelist, target, walk)
        recipe = _keyed_row(walk.pieces) if walk.looped else None
        return cls(wrapper_tag("".join(parts), _WRAPPER), recipe)


type _Placed = tuple[Template | None, tuple[NodeList, ...] | None, _Placement]


@dataclass(slots=True)
class _Known:
    """What one call works out once and keeps, outside the node a template shares."""

    site: str = ""
    key: str = ""
    placed: _Placed | None = None


_known: WeakKeyDictionary[ComponentNode, _Known] = WeakKeyDictionary()


@dataclass(frozen=True, slots=True)
class _SlotCollector:
    """The slot dict a rendering component offers to the slots it owns."""

    owner: ComponentNode
    slots: dict[str, str]


class SlotNode(SlotTagNode):
    """Fills a named slot of the component whose body holds it."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Remember the slot name and nested nodes."""
        self.name = name
        self.nodelist = nodelist
        # Claimed by the compiling component, so a slot via include fills nothing.
        self.owner: ComponentNode | None = None

    def collect(self, context: template.Context, slots: dict[str, str]) -> None:
        """Append the rendered body to the entry this slot name holds.

        A repeat appends so a slot in ``{% for %}`` keeps every body in render order.
        """
        body = self.nodelist.render(context)
        previous = slots.get(self.name)
        slots[self.name] = body if previous is None else previous + body

    @override
    def render(self, context: template.Context) -> str:
        """Fill the owning component's slot, or render the body where it stands.

        A slot whose owner is not the component rendering around it renders in place.
        """
        collector = context.get(_SLOT_COLLECTOR_KEY)
        if isinstance(collector, _SlotCollector) and collector.owner is self.owner:
            self.collect(context, collector.slots)
            return ""
        return self.nodelist.render(context)


class ComponentNode(ComponentTagNode):
    """Looks up the component, gathers slots and free children, then renders HTML."""

    def __init__(
        self,
        name: str,
        props: dict[str, FilterExpression | Interpolation],
        nodelist: NodeList,
        *,
        options: BoundaryOptions | None = None,
        index: int = 0,
        captured: bool = False,
        perms: tuple[str, ...] | None = (),
    ) -> None:
        """Store component name, prop expressions, boundary options and nested nodes.

        `index` places the call among the component calls of its template, `captured`
        marks one compiled inside a tag that stores what it renders, and `perms` holds
        what the `{% has_perm %}` branches granting it name, `None` for an unnamed one.
        """
        self.name = name
        self.props = props
        self.nodelist = nodelist
        self.options = options
        self.index = index
        self.captured = captured
        self.perms = perms
        # Prop names are fixed at compile time, so the guard rebuilds nothing.
        self.prop_names = frozenset(props)
        # Slots are fixed once compiled, so walk once, nested ones claim their own.
        claimed = [
            node
            for node in cast("list[SlotNode]", nodelist.get_nodes_by_type(SlotNode))
            if node.owner is None
        ]
        for node in claimed:
            node.owner = self
        self.has_slots = bool(claimed)

    def constant_props(self) -> dict[str, object]:
        """Return the props this call passes as constants, which a check can read."""
        found = {name: _constant(expr) for name, expr in self.props.items()}
        return {
            name: value for name, value in found.items() if value is not _NOT_LITERAL
        }

    def _resolved_props(self, context: template.Context) -> dict[str, Any]:
        """Resolve every prop expression against the active template context.

        ``FilterExpression`` marks a string literal safe, so raw HTML needs ``|safe``.
        """
        resolved: dict[str, Any] = {}
        for key, expr in self.props.items():
            if isinstance(expr, Interpolation):
                resolved[key] = expr.resolve(context)
                continue
            value = expr.resolve(context)
            is_literal = not isinstance(expr.var, Variable)
            if is_literal and not expr.filters and isinstance(value, SafeString):
                # Django marks a bare literal safe, demote it so it autoescapes.
                value = str.__str__(value)
            resolved[key] = value
        return resolved

    def _template_path_from_context(self, context: template.Context) -> Path | None:
        """Return a resolved path from ``current_template_path``, or ``None``."""
        raw = context.get(TEMPLATE_PATH_KEY)
        # A ``Path`` was resolved by an earlier render, only ``str`` hits the disk.
        if isinstance(raw, Path):
            return raw
        if isinstance(raw, str):
            return _resolve_template_path(raw)
        return None

    def _close_match(self, path: Path) -> str | None:
        """Return the closest visible component name for a did-you-mean hint.

        The failed `get_component` lookup just built the visibility map, a cache hit.
        """
        names = collect_visible_components(path)
        matches = difflib.get_close_matches(self.name, sorted(names))
        return matches[0] if matches else None

    def _on_missing_path(self) -> str:
        """Report a render whose context carries no `current_template_path`.

        The context names no template, so there is no scope for a did-you-mean hint.
        """
        if not fail_loudly():
            logger.warning(
                "component '%s' skipped, no current_template_path in context", self.name
            )
            return ""
        if next_framework_settings.STRICT_LOADING:
            msg = (
                f"component '{self.name}' cannot be resolved because the "
                "template context has no current_template_path"
            )
            raise template.TemplateSyntaxError(msg)
        return (
            f"<!-- next: component '{_comment_safe(self.name)}' skipped, no "
            "current_template_path in context (no-discovery-path) -->"
        )

    def _on_not_found(self, path: Path) -> str:
        """Report a name no component visible from `path` answers."""
        if not fail_loudly():
            logger.warning("component '%s' not found from %s", self.name, path)
            return ""
        suggestion = self._close_match(path)
        if next_framework_settings.STRICT_LOADING:
            hint = f", did you mean '{suggestion}'?" if suggestion else ""
            msg = f"component '{self.name}' not found from {path}{hint}"
            raise template.TemplateSyntaxError(msg)
        hint = f", did you mean '{_comment_safe(suggestion)}'?" if suggestion else ""
        return (
            f"<!-- next: component '{_comment_safe(self.name)}' not found "
            f"(not-found){hint} -->"
        )

    def _render_children(
        self, context: template.Context, slots: dict[str, str]
    ) -> SafeString:
        """Render the body, routing every slot body into `slots`.

        A body holding no slot skips the collector push.
        """
        if not self.has_slots:
            return self.nodelist.render(context)
        with context.push(**{_SLOT_COLLECTOR_KEY: _SlotCollector(self, slots)}):
            return self.nodelist.render(context)

    def _captured(self, context: template.Context) -> bool:
        """Whether an include a `{% cache %}` body holds rendered this call.

        The render context names no include, so a template included both inside and
        outside such a body counts as captured at both.
        """
        current = context.render_context.template
        top = context.template
        if current is None or top is None or current is top:
            return False
        includes = _captured_includes(top)
        return bool(includes) and _included(
            self, includes, context.render_context.dicts[0]
        )

    @override
    def render(self, context: template.Context) -> str:
        """Render the component, every deferred call inside a stored render in place.

        A call compiled inside a tag that stores its output knows it, an included one
        reads the render once the project can defer at all.
        """
        if not _capturing.get() and (
            self.captured or (deferred_components.held and self._captured(context))
        ):
            token = _capturing.set(True)
            try:
                return self._render(context)
            finally:
                _capturing.reset(token)
        return self._render(context)

    def _render(self, context: template.Context) -> str:
        """Merge props, slots, and children, then render the component."""
        path = self._template_path_from_context(context)
        if path is None:
            return self._on_missing_path()

        info = get_component(self.name, path)
        if info is None:
            return self._on_not_found(path)

        files = info.files
        call = self.options
        mode = None if call is None else call.mode
        if mode is None:
            declared_options = defer_declarations.options(info.module_path)
            mode = None if declared_options is None else declared_options.mode
        if mode is None and files is not None and files.loading is not None:
            mode = DeferMode.STREAM
        if mode is not None:
            return self._render_deferred(context, info, path, mode)

        if files is not None and files.error is not None:
            return self._render_guarded(context, info, path, files.error)

        if ruled(info):
            attempt = Attempt(info)
            token = current_attempt.set(attempt)
            try:
                return self.attempt(context, info, path, attempt)
            finally:
                current_attempt.reset(token)
        collect_component_assets(info, context.get(COLLECTOR_KEY))
        return self._composed(context, info, path)

    def _composed(
        self,
        context: template.Context,
        info: ComponentInfo,
        path: Path,
        props: dict[str, Any] | None = None,
    ) -> str:
        """Render the children and the slots, then the component over its props."""
        slots: dict[str, str] = {}
        children = self._render_children(context, slots)

        # ``flatten`` returns a fresh dict, so the render context is filled in place.
        render_ctx = cast("dict[str, Any]", context.flatten())
        for key in _INTERNAL_CONTEXT_KEYS:
            render_ctx.pop(key, None)
        render_ctx.update(self._resolved_props(context) if props is None else props)
        render_ctx[TEMPLATE_PATH_KEY] = path
        # Children are finished markup inserted unescaped, the calling template escapes.
        render_ctx["children"] = children
        render_ctx[COMPONENT_PROPS_CONTEXT_KEY] = self.prop_names

        for slot_name, content in slots.items():
            # Slot content lives under ``slot_<name>`` so it cannot shadow a prop.
            render_ctx[f"{SLOT_KEY_PREFIX}{slot_name}"] = content

        request = render_ctx.get(REQUEST_KEY)
        if request is None:
            request = context.get(REQUEST_KEY)
        return render_component(info, render_ctx, request=request)

    @property
    def site(self) -> str:
        """Return the template and the line of this call, which messages name."""
        known = self._known()
        if not known.site:
            origin = getattr(self, "origin", None)
            token = self.token
            where = "an unnamed template" if origin is None else origin.name
            line = "" if token is None else f", line {token.lineno}"
            known.site = f"the call of component {self.name!r} in {where}{line}"
        return known.site

    def _known(self) -> _Known:
        """Return what this call keeps once worked out, created on first use."""
        held = _known.get(self)
        if held is None:
            held = _known[self] = _Known()
        return held

    def _placement(self, context: template.Context) -> _Placement:
        """Return what the static markup before this call decides, kept per site.

        A call inside blocks keeps its answer while the same blocks render around it.
        """
        current = context.render_context.template
        known = self._known()
        held = known.placed
        if (
            held is not None
            and held[0] is current
            and (held[1] is None or held[1] == _nodelists(_blocks(context)))
        ):
            return held[2]
        if current is not None and _finds(current.nodelist, self._is_self):
            placed = _Placement.of(((current.nodelist, self._is_self),))
            known.placed = (current, None, placed)
            return placed
        blocks = _blocks(context)
        placed = _Placement.of(self._path(current, blocks))
        known.placed = (current, _nodelists(blocks), placed)
        return placed

    def _path(
        self, current: Template | None, blocks: tuple[BlockNode, ...]
    ) -> tuple[tuple[NodeList, Callable[[Node], bool]], ...]:
        """Return each nodelist from the rendered template down to this call.

        A child block continues the text its parent puts before it, an include does not.
        """
        if not blocks or not _finds(blocks[-1].nodelist, self._is_self):
            return ()
        path: list[tuple[NodeList, Callable[[Node], bool]]] = [
            (blocks[-1].nodelist, self._is_self)
        ]
        name = blocks[-1].name
        for block in reversed(blocks[:-1]):
            placing = _placing(name)
            if _finds(block.nodelist, placing):
                path.append((block.nodelist, placing))
                name = block.name
        placing = _placing(name)
        if current is not None and _finds(current.nodelist, placing):
            path.append((current.nodelist, placing))
        return tuple(reversed(path))

    def _is_self(self, node: Node) -> bool:
        return node is self

    def _render_guarded(
        self, context: template.Context, info: ComponentInfo, path: Path, error: Path
    ) -> str:
        """Render the whole subtree inside the error boundary of the component.

        Under `DEBUG` the error UI names the `error.djx` it came from.
        """
        attempt = Attempt(info)
        source = shown_source(error) if settings.DEBUG else None
        fallback = _ComponentFallback(self, context, attempt, path)
        token = current_attempt.set(attempt)
        try:
            return contain(
                self.guard(context, info, source),
                functools.partial(self.attempt, context, info, path, attempt),
                fallback,
            )
        finally:
            current_attempt.reset(token)

    def guard(
        self,
        context: template.Context,
        info: ComponentInfo,
        source: str | None,
        ident: str | None = None,
    ) -> Boundary:
        """Return the error boundary of this call, `ident` the wrapper around it."""
        return Boundary(
            info.name,
            ADDRESSING,
            request=context.get(REQUEST_KEY),
            collector=context.get(COLLECTOR_KEY),
            owner=info,
            site=self.site,
            source=source,
            ident=ident,
        )

    def attempt(
        self,
        context: template.Context,
        info: ComponentInfo,
        path: Path,
        attempt: Attempt,
        props: dict[str, Any] | None = None,
    ) -> str:
        """Render the subtree of a guarded call, its assets and props noted on it.

        A deferred call passes the props it resolved at its call site. The assets of a
        call its access rule denies roll back, as an inline denial shows nothing.
        """
        if props is None:
            props = self._resolved_props(context)
        attempt.props = props
        collector = context.get(COLLECTOR_KEY)
        if not isinstance(collector, StaticCollector) or not ruled(info):
            collect_component_assets(info, collector)
            return self._composed(context, info, path, props)
        mark = collector.mark()
        try:
            collect_component_assets(info, collector)
            html = self._composed(context, info, path, props)
        except BaseException:
            collector.rollback(mark)
            raise
        if attempt.granted:
            collector.release()
        else:
            collector.rollback(mark)
        return html

    def wrapper(self, context: template.Context, info: ComponentInfo) -> str | None:
        """Return the declared wrapper, else the one the markup before the call allows.

        A declared tag overrides the site, `None` forcing an adopted root.
        """
        options = defer_declarations.options(info.module_path)
        if options is not None and options.tag_declared:
            return options.tag
        return self._placement(context).tag

    def file_context(self, context: template.Context, path: Path) -> dict[str, Any]:
        """Return the call-site context a `loading.djx` or an `error.djx` reads."""
        render_ctx = cast("dict[str, Any]", context.flatten())
        for key in _INTERNAL_CONTEXT_KEYS:
            render_ctx.pop(key, None)
        render_ctx[TEMPLATE_PATH_KEY] = path
        return render_ctx

    def _render_deferred(
        self,
        context: template.Context,
        info: ComponentInfo,
        path: Path,
        mode: DeferMode,
    ) -> str:
        """Hand the call to the sink, a shell access rule refusing it outright.

        Props that fail to resolve render the call in place, behind its error boundary,
        and so does a call inside a loading UI. A stored render keeps the call inline.
        """
        may_defer()
        if client_mode(mode) and not _capturing.get() and not in_loading():
            return self._render_trip(context, info, path, mode)
        in_job = False
        module_path = info.module_path
        if module_path is not None:
            rule = declared(module_path)
            in_job = any(need.obj is not None for need in rule.requirements)
            user = getattr(context.get(REQUEST_KEY), "user", None)
            if not in_job and check(user, rule) is not None:
                return ""
        props: dict[str, Any] | None = None
        with Capture():
            props = self._resolved_props(context)
        tag = self.wrapper(context, info)
        retry = None
        if props is not None and (addressable(info) or client_mode(mode)):
            retry = self.token_input(info, mode, props)
        renderer = _DeferredRender(
            self, info, path, props, granted=not in_job, tag=tag, trip=retry
        )
        sink = deferral_sink(context)
        if props is None or sink is None or in_loading():
            return renderer.alone(context)
        stored = mode is DeferMode.INLINE or _capturing.get()
        delivered = DeferMode.INLINE if stored else DeferMode.STREAM
        call = DeferredCall(
            site=Site(
                Addressing.COMPONENT, info.name, delivered, self._origin(), self.index
            ),
            options=self._options(info, delivered),
            key=self._boundary_key(context),
            tag=tag,
            source=_source(info),
            renderer=renderer,
        )
        if delivered is DeferMode.INLINE:
            return sink.inline(context, call) + stash_loading(context, call)
        if not in_job:
            collect_component_assets(info, context.get(COLLECTOR_KEY))
        return sink.queue(context, call)

    def _render_trip(
        self,
        context: template.Context,
        info: ComponentInfo,
        path: Path,
        mode: DeferMode,
    ) -> str:
        """Hand a client-trip call to the sink, its rule checked on the call's props.

        The component itself never runs here, and a denied call leaves nothing behind.
        """
        self._refuse_shape(info)
        props: dict[str, Any] | None = None
        with Capture():
            props = self._resolved_props(context)
        tag = self.wrapper(context, info)
        if props is None:
            renderer = _DeferredRender(self, info, path, None, granted=False, tag=tag)
            return renderer.alone(context)
        user = getattr(context.get(REQUEST_KEY), "user", None)
        if info.module_path is not None and (
            check(user, declared(info.module_path), _lookup(props)) is not None
        ):
            return ""
        trip = self.token_input(info, mode, props)
        renderer = _DeferredRender(
            self, info, path, props, granted=True, tag=tag, trip=trip
        )
        sink = deferral_sink(context)
        if sink is None:
            return renderer.alone(context)
        call = DeferredCall(
            site=Site(
                Addressing.COMPONENT, info.name, mode, self._origin(), self.index
            ),
            options=self._options(info, mode),
            key=self._boundary_key(context),
            tag=tag,
            source=_source(info),
            renderer=renderer,
        )
        return sink.queue(context, call)

    def _refuse_shape(self, info: ComponentInfo) -> None:
        """Refuse a client-trip call its token cannot stand for.

        Its props must be declared, it takes no children or slots, and the guards around
        it must name their permissions, as a reload checks those names alone.
        """
        site = self.site[:1].upper() + self.site[1:]
        if claimed(self.perms, rendering_guards()) is None:
            msg = (
                f"{site} sits in a {{% has_perm %}} branch guarded by a variable or an "
                "object, which the token of a client-trip component cannot carry for "
                "a reload to check. Guard it by a literal permission name."
            )
            raise template.TemplateSyntaxError(msg)
        undeclared = sorted(self.prop_names - set(declared_props(info)))
        if undeclared:
            msg = (
                f"{site} passes the undeclared props {', '.join(undeclared)} to a "
                "client-trip component (next.E224). Declare them with "
                "component.defer(..., props=(...))."
            )
            raise template.TemplateSyntaxError(msg)
        if self.has_slots or any(map(_significant, self.nodelist)):
            msg = (
                f"{site} gives children or slots to a client-trip component, "
                "which renders on a later request from its props alone (next.E224)."
            )
            raise template.TemplateSyntaxError(msg)

    def token_input(
        self, info: ComponentInfo, mode: DeferMode, props: dict[str, Any]
    ) -> TripInput | None:
        """Return what a token of this call carries, `None` for unnamed guards.

        The guards are those this call compiled inside and those rendering around it.
        """
        perms = claimed(self.perms, rendering_guards())
        if perms is None:
            return None
        names = declared_props(info)
        return TripInput(
            info, mode, {name: props[name] for name in names if name in props}, perms
        )

    def _origin(self) -> str:
        origin = getattr(self, "origin", None)
        return "" if origin is None else str(origin.name)

    def _anchor(self) -> str:
        """Return the template of this call as a key hashes it, stable across hosts."""
        origin = getattr(self, "origin", None)
        if origin is None:
            return ""
        name = origin.template_name
        return name if isinstance(name, str) else _anchored(str(origin.name))

    def _options(self, info: ComponentInfo, mode: DeferMode) -> BoundaryOptions:
        """Return the options the call runs under, the call site over the module."""
        written = defer_declarations.options(info.module_path) or BoundaryOptions()
        call = self.options
        if call is None:
            return replace(written, mode=mode)
        timeout = written.timeout if call.timeout is None else call.timeout
        return replace(
            written,
            mode=mode,
            timeout=timeout,
            reload=written.reload if call.reload is None else call.reload,
            delay=written.delay if call.delay is None else call.delay,
        )

    def _site_key(self, context: template.Context) -> str:
        """Return the key of this call site, an included one counted by its render.

        A template included twice holds one node, so each render past the first adds
        how many renders of the node came before it in the response.
        """
        known = self._known()
        if not known.key:
            known.key = stable_key(self._anchor(), self.index)
        current = context.render_context.template
        if current is None or current is context.template:
            return known.key
        seen = cast("dict[object, int]", context.render_context.dicts[0])
        slot = (_ORDINAL, self)
        count = seen.get(slot, 0)
        seen[slot] = count + 1
        if not count:
            return known.key
        return stable_key(self._anchor(), f"{self.index}.{count}")

    def _boundary_key(self, context: template.Context) -> str:
        """Return the stable key, with the keyed row around a loop call appended."""
        key = self._site_key(context)
        recipe = self._placement(context).recipe
        if recipe is None:
            return key
        row = "".join(
            piece if isinstance(piece, str) else str(piece.resolve(context))
            for piece in recipe
        )
        return f"{key}:{row}"


def _significant(node: Node) -> bool:
    """Whether `node` is more than whitespace between the tags of a component."""
    return not isinstance(node, TextNode) or bool(node.s.strip())


def _lookup(props: dict[str, Any]) -> Callable[[object], object]:
    """Return the object lookup of a rule checked on call-site props alone."""

    def lookup(name: object) -> object:
        return props.get(name) if isinstance(name, str) else None

    return lookup


def _source(info: ComponentInfo) -> str | None:
    """Return the file a `DEBUG` wrapper names, the loading UI before the error UI."""
    if not settings.DEBUG:
        return None
    files = info.files
    owner = None if files is None else files.loading or files.error
    owner = owner or info.module_path or info.template_path
    return None if owner is None else shown_source(owner)


class _DeferredRender:
    """How one deferred call renders its loading UI, its body and its error UI."""

    __slots__ = (
        "_granted",
        "_guards",
        "_info",
        "_node",
        "_passed",
        "_path",
        "_props",
        "_tag",
        "_trip",
    )

    def __init__(
        self,
        node: ComponentNode,
        info: ComponentInfo,
        path: Path,
        props: dict[str, Any] | None,
        *,
        granted: bool,
        tag: str | None,
        trip: TripInput | None = None,
    ) -> None:
        """Hold the call with the props it resolved and whether its rule passed.

        `None` props resolve again inside the boundary, where their failure is caught,
        and `trip` is what a token of the call carries.
        """
        self._node = node
        self._info = info
        self._path = path
        self._props = props
        self._granted = granted
        self._tag = tag
        self._trip = trip
        self._passed = granted or not ruled(info)
        held = rendering_guards()
        self._guards: Guards = held if trip is None else (*held, *trip.perms)

    def trip_input(self) -> TripInput | None:
        """Return the input of a client-trip shell, `None` for another call."""
        trip = self._trip
        return trip if trip is not None and client_mode(trip.mode) else None

    def retry_input(self, failure: BoundaryError) -> TripInput | None:
        """Return the input a retry token is minted from, once the rule passed."""
        if not failure.retry or failure.kind in UNDISCLOSED or not self._passed:
            return None
        return self._trip

    def _rooted(self, html: str) -> tuple[str, BoundaryError | None]:
        """Return `html`, or the error a site adopting its root settles a misfit as.

        Such a site takes one root element, an empty body standing for a denied one.
        """
        if self._tag is not None or not html.strip():
            return html, None
        found = misrooted(html)
        if found is None:
            return html, None
        shown = new_digest()
        logger.error(_MISROOTED_LOG, self._node.site, found, shown)
        mark_degraded()
        error = BoundaryError.of(
            ErrorKind.ERROR,
            shown,
            retry=retry_loads(),
            message=_MISROOTED % (self._node.site, found),
            traceback="",
        )
        return "", error

    def _attempt(self) -> Attempt:
        attempt = Attempt(self._info)
        attempt.props = self._props
        attempt.granted = self._granted
        return attempt

    def _contained(
        self, context: template.Context, attempt: Attempt, render: Callable[[], str]
    ) -> tuple[str, tuple[str, BoundaryError] | None]:
        """Run `render` inside the error boundary, with the error UI it fell back to.

        Its boundary claims the id of the wrapper around it, which a failure names.
        """
        fallback = _ComponentFallback(
            self._node, context, attempt, self._path, deferred=True
        )
        guard = self._node.guard(context, self._info, None, claim_wrapper())
        token = current_attempt.set(attempt)
        try:
            with rendered_under(self._guards):
                html = contain(guard, render, fallback)
        finally:
            current_attempt.reset(token)
        return html, fallback.settled

    def _component(
        self, context: template.Context
    ) -> tuple[str, tuple[str, BoundaryError] | None]:
        attempt = Attempt(self._info)
        render = functools.partial(
            self._node.attempt, context, self._info, self._path, attempt, self._props
        )
        try:
            return self._contained(context, attempt, render)
        finally:
            self._passed = self._passed or attempt.granted

    def alone(self, context: template.Context) -> str:
        """Render the component in place behind its error boundary, without a sink."""
        return self._component(context)[0]

    def loading(self, context: template.Context) -> tuple[str, BoundaryError | None]:
        """Render the loading UI with the props a passed rule discloses.

        A deferred call inside it renders in place, as nothing assembles a skeleton.
        """
        attempt = self._attempt()

        def render() -> str:
            render_ctx = self._node.file_context(context, self._path)
            props = attempt.disclosed(ErrorKind.ERROR)
            if props is None:
                render_ctx.pop("props", None)
            else:
                render_ctx["props"] = props
            with loading_content():
                return render_component_loading(
                    self._info, render_ctx, request=context.get(REQUEST_KEY)
                )

        html, settled = self._contained(context, attempt, render)
        return self._rooted(html if settled is None else settled[0])

    def body(self, context: template.Context) -> tuple[str, BoundaryError | None]:
        """Render the component from the props of the call, the error UI unwrapped."""
        html, settled = self._component(context)
        return self._rooted(html) if settled is None else settled

    def error(
        self, context: template.Context, kind: ErrorKind
    ) -> tuple[str, BoundaryError]:
        """Render the error UI of `kind` without running the component."""
        attempt = self._attempt()
        fallback = _ComponentFallback(
            self._node, context, attempt, self._path, deferred=True
        )
        error = unrun_error(kind)
        return fallback.render(error, kind), error


def isolated_renderer(
    info: ComponentInfo, path: Path, trip: TripInput
) -> _DeferredRender:
    """Return the renderer of one component from its props alone, as a reload runs it.

    Its access rule already passed, its call site is gone, so its declared tag wraps it.
    """
    node = ComponentNode(info.name, {}, NodeList())
    node.prop_names = frozenset(trip.props)
    options = defer_declarations.options(info.module_path)
    tag = options.tag if options is not None and options.tag_declared else _WRAPPER
    return _DeferredRender(
        node, info, path, dict(trip.props), granted=True, tag=tag, trip=trip
    )


def _placing(name: str) -> Callable[[Node], bool]:
    """Return the test that picks the `{% block %}` named `name`."""

    def placing(node: Node) -> bool:
        return isinstance(node, BlockNode) and node.name == name

    return placing


class _ComponentFallback:
    """The error UI of one component call, rendered from the call-site context.

    A deferred call falls back to the project default and keeps what it rendered.
    """

    __slots__ = ("_attempt", "_context", "_deferred", "_node", "_path", "settled")

    def __init__(
        self,
        node: ComponentNode,
        context: template.Context,
        attempt: Attempt,
        path: Path,
        *,
        deferred: bool = False,
    ) -> None:
        """Hold the call site the error UI renders from."""
        self._node = node
        self._context = context
        self._attempt = attempt
        self._path = path
        self._deferred = deferred
        self.settled: tuple[str, BoundaryError] | None = None

    def retry(self) -> bool:
        """Return whether the runtime script loads, which every retry needs."""
        return retry_loads()

    def retry_token(self, error: BoundaryError) -> str:
        """Return the retry token of a plain call declaring props whose rule passed.

        A deferred call gets its token from the sink that settles its wrapper instead.
        """
        attempt = self._attempt
        info, props = attempt.info, attempt.props
        if (
            self._deferred
            or not addressable(info)
            or props is None
            or not error.retry
            or error.kind in UNDISCLOSED
            or not (attempt.granted or not ruled(info))
        ):
            return ""
        context = self._context
        sink = deferral_sink(context)
        trip = self._node.token_input(info, DeferMode.INLINE, props)
        if sink is None or trip is None:
            return ""
        return sink.retry(context.get(REQUEST_KEY), trip)

    def tag(self) -> str | None:
        """Return the wrapper the call allows, `None` forcing an adopted root."""
        return self._node.wrapper(self._context, self._attempt.info)

    def render(self, error: BoundaryError, kind: ErrorKind) -> str:
        """Render `error.djx` from the call-site context, `error` and the props."""
        context = self._context
        render_ctx = self._node.file_context(context, self._path)
        render_ctx["error"] = error
        props = self._attempt.disclosed(kind)
        if props is None:
            render_ctx.pop("props", None)
        else:
            render_ctx["props"] = props
        html = render_component_error(
            self._attempt.info,
            render_ctx,
            request=context.get(REQUEST_KEY),
            deferred=self._deferred,
        )
        self.settled = (html, error)
        return html


class SetSlotNode(SetSlotTagNode):
    """Uses injected slot HTML from the parent, or renders the fallback body."""

    def __init__(self, name: str, nodelist: NodeList) -> None:
        """Remember slot name and fallback nodes."""
        self.name = name
        self.nodelist = nodelist

    @override
    def render(self, context: template.Context) -> str:
        """Render injected slot HTML when present, otherwise the fallback body.

        Only a missing key falls back, not an explicitly empty slot.
        """
        slot_content = context.get(f"{SLOT_KEY_PREFIX}{self.name}", _SLOT_MISSING)
        if slot_content is _SLOT_MISSING:
            return self.nodelist.render(context)
        return str(slot_content)


@register.tag(name="component")
def do_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile void ``{% component "name" … %}`` (no body, no closing tag)."""
    call = _parse_component(parser, token, "{% component %}")
    return ComponentNode(
        call.name,
        call.props,
        NodeList(),
        options=call.options,
        index=call.index,
        captured=call.captured,
        perms=call.perms,
    )


@register.tag(name="#component")
def do_block_component(parser: Parser, token: Token) -> ComponentNode:
    """Compile ``{% #component "name" … %}`` … ``{% /component %}``."""
    call = _parse_component(parser, token, "{% #component %}")
    nodelist = parser.parse(_END_BLOCK_COMPONENT)
    parser.delete_first_token()
    return ComponentNode(
        call.name,
        call.props,
        nodelist,
        options=call.options,
        index=call.index,
        captured=call.captured,
        perms=call.perms,
    )


_BLOCK_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% #slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #slot %} tag requires exactly one argument: slot name",
    end_tokens=_END_BLOCK_SLOT,
)

_SHORT_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% slot %} tag requires a quoted slot name",
    wrong_arity_message="{% slot %} short form requires exactly one quoted slot name",
)

_SET_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% #set_slot %} tag requires a quoted slot name",
    wrong_arity_message="{% #set_slot %} tag requires exactly one argument: slot name",
    end_tokens=_END_BLOCK_SET_SLOT,
)

_SHORT_SET_SLOT_SPEC = _NamedTagSpec(
    empty_name_message="{% set_slot %} tag requires a quoted slot name",
    wrong_arity_message=(
        "{% set_slot %} short form requires exactly one quoted slot name"
    ),
)


@register.tag(name="#slot")
def do_block_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile ``{% #slot "name" %}`` … ``{% /slot %}``."""
    name, nodelist = _parse_named_tag(parser, token, _BLOCK_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="slot")
def do_short_slot(parser: Parser, token: Token) -> SlotNode:
    """Compile empty ``{% slot "name" %}`` (short slot, no body)."""
    name, nodelist = _parse_named_tag(parser, token, _SHORT_SLOT_SPEC)
    return SlotNode(name=name, nodelist=nodelist)


@register.tag(name="#set_slot")
def do_block_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile ``{% #set_slot "name" %}`` … ``{% /set_slot %}``."""
    name, nodelist = _parse_named_tag(parser, token, _SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)


@register.tag(name="set_slot")
def do_short_set_slot(parser: Parser, token: Token) -> SetSlotNode:
    """Compile empty ``{% set_slot "name" %}`` (no default body, no closing tag)."""
    name, nodelist = _parse_named_tag(parser, token, _SHORT_SET_SLOT_SPEC)
    return SetSlotNode(name=name, nodelist=nodelist)
