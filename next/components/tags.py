"""The nodes `{% component %}` and its slot tags compile to, and how a call renders.

Props and slot HTML resolve from ``current_template_path`` and pass to the renderer.
"""

from __future__ import annotations

import difflib
import functools
import logging
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, cast, override
from weakref import WeakKeyDictionary

from django import template
from django.conf import settings
from django.template.base import FilterExpression, Node, NodeList, Template, Variable
from django.utils.safestring import SafeString

from next.boundaries import contain
from next.boundaries.calls import Addressing, BoundarySite, DeferredCall, stable_key
from next.boundaries.failures import Boundary, shown_source
from next.boundaries.options import DEFAULT_TAG, BoundaryOptions, DeferMode
from next.boundaries.stash import in_loading, stash_loading
from next.conf import fail_loudly, next_framework_settings
from next.diagnostics import Capture
from next.interpolation import Interpolation
from next.marks import marked
from next.permissions.access import declarations
from next.permissions.nodes import claimed, rendering_guards
from next.ports import static_assets_slot
from next.seeding import COLLECTOR_KEY, REQUEST_KEY, TEMPLATE_PATH_KEY

from .access import PENDING, decide, ruled
from .boundaries import Attempt, current_attempt, deferral_sink
from .calls import ComponentErrorUI, DeferredRender, props_lookup, wrapper_source
from .defer import defer_declarations
from .facade import collect_visible_components, get_component, render_component
from .info import CallRoute
from .nodes import ComponentTagNode, SetSlotTagNode, SlotTagNode
from .parsing import Absent, constant
from .paths import anchored, resolve_template_path
from .placement import (
    Known,
    Placement,
    block_named,
    captured_includes,
    finds,
    included,
    nodelists,
    rendering_blocks,
    same,
    significant,
)
from .registry import deferred_components, may_defer
from .renderers import (
    COMPONENT_PROPS_CONTEXT_KEY,
    SLOT_KEY_PREFIX,
    ComponentScope,
    comment_safe,
)
from .trip import TripInput, addressable, client_mode, declared_props


if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from django.template.loader_tags import BlockNode

    from next.static import StaticCollector

    from .info import ComponentInfo


logger = logging.getLogger(__name__)

# The key a component pushes for its body to write slots into.
_SLOT_COLLECTOR_KEY = "_component_slots"

# Body-only keys. ``_component_props`` is not listed as every node overwrites it.
_INTERNAL_CONTEXT_KEYS = frozenset({_SLOT_COLLECTOR_KEY})

_ORDINAL: Final = "next.component.ordinal"
_capturing: ContextVar[bool] = ContextVar("next_component_capturing", default=False)
_known: WeakKeyDictionary[ComponentNode, Known] = WeakKeyDictionary()


def _collect(info: ComponentInfo, collector: StaticCollector | None) -> None:
    """Collect the co-located assets of a composite `info` into `collector`."""
    if collector is not None and not info.is_simple:
        static_assets_slot.get().collect_component_assets(info, collector)


def _route(info: ComponentInfo) -> CallRoute:
    """Return how a call of `info` that names no mode renders, decided once per stamp.

    A `loading.djx` streams the call when its `component.py` declares no mode.
    """
    stamp = (defer_declarations, defer_declarations.version, declarations.version)
    held = info.route
    if held is not None and held.stamp == stamp:
        return held
    declared_options = defer_declarations.options(info.module_path)
    mode = None if declared_options is None else declared_options.mode
    files = info.files
    if mode is None and files is not None and files.loading is not None:
        mode = DeferMode.STREAM
    route = CallRoute(stamp, mode, ruled(info))
    info.hold_route(route)
    return route


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
    """Looks up the component, gathers slots and free children, then renders HTML.

    `perms` names what the granting `{% has_perm %}` branches check, `None` if unnamed.
    """

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
        """Store the call as compiled, `captured` inside a tag storing its output."""
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
        found = {name: constant(expr) for name, expr in self.props.items()}
        return {
            name: value for name, value in found.items() if value is not Absent.LITERAL
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
            return resolve_template_path(raw)
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
            f"<!-- next: component '{comment_safe(self.name)}' skipped, no "
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
        hint = f", did you mean '{comment_safe(suggestion)}'?" if suggestion else ""
        return (
            f"<!-- next: component '{comment_safe(self.name)}' not found "
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
        includes = captured_includes(top)
        return bool(includes) and included(
            self, includes, context.render_context.dicts[0]
        )

    @override
    def render(self, context: template.Context) -> str:
        """Render the component, every deferred call inside a stored render in place.

        A call compiled inside a tag that stores its output knows it, an included one
        reads the render once the project can defer at all.
        """
        if (
            self.captured or (deferred_components.held and self._captured(context))
        ) and not _capturing.get():
            token = _capturing.set(True)
            try:
                return self.render(context)
            finally:
                _capturing.reset(token)
        path = self._template_path_from_context(context)
        if path is None:
            return self._on_missing_path()

        info = get_component(self.name, path)
        if info is None:
            return self._on_not_found(path)

        call = self.options
        route = _route(info)
        mode = route.mode if call is None or call.mode is None else call.mode
        files = info.files
        if (
            mode is not None
            or route.ruled
            or (files is not None and files.error is not None)
        ):
            return marked(functools.partial(self._bounded, context, info, path, mode))
        collector = context.get(COLLECTOR_KEY)
        if collector is not None and not info.is_simple:
            static_assets_slot.get().collect_component_assets(info, collector)
        if not self.nodelist:
            # The renderer marks the links, or leaves them to the template's containers.
            return self._composed(context, info, path)
        return marked(functools.partial(self._composed, context, info, path))

    def _bounded(
        self,
        context: template.Context,
        info: ComponentInfo,
        path: Path,
        mode: DeferMode | None,
    ) -> str:
        """Render a call that defers, sits behind an error boundary, or has a rule."""
        if mode is not None:
            return self._render_deferred(context, info, path, mode)
        files = info.files
        if files is not None and files.error is not None:
            return self._render_guarded(context, info, path, files.error)
        attempt = Attempt(info)
        token = current_attempt.set(attempt)
        try:
            return self.attempt(context, info, path, attempt)
        finally:
            current_attempt.reset(token)

    def _composed(
        self,
        context: template.Context,
        info: ComponentInfo,
        path: Path,
        props: dict[str, Any] | None = None,
    ) -> str:
        """Render the children and the slots, then the component over its props.

        The component reads the call-site layers in place and writes a layer of its own.
        """
        slots: dict[str, str] = {}
        children = self._render_children(context, slots)

        own = self._resolved_props(context) if props is None else dict(props)
        own[TEMPLATE_PATH_KEY] = path
        # Children are finished markup inserted unescaped, the calling template escapes.
        own["children"] = children
        own[COMPONENT_PROPS_CONTEXT_KEY] = self.prop_names

        for slot_name, content in slots.items():
            # Slot content lives under ``slot_<name>`` so it cannot shadow a prop.
            own[f"{SLOT_KEY_PREFIX}{slot_name}"] = content

        scope = ComponentScope(own, context.dicts[1:])
        return render_component(info, scope, request=scope.get(REQUEST_KEY))

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

    def _known(self) -> Known:
        """Return what this call keeps once worked out, created on first use."""
        held = _known.get(self)
        if held is None:
            held = _known[self] = Known()
        return held

    def _placement(self, context: template.Context) -> Placement:
        """Return what the static markup before this call decides, kept per template.

        A call inside blocks keeps its answer while the same blocks render around it.
        """
        current = context.render_context.template
        if current is None:
            return Placement.of(self._path(None, rendering_blocks(context)))
        placed = self._known().placed
        held = placed.get(current)
        if held is not None and (
            held[0] is None or same(held[0], nodelists(rendering_blocks(context)))
        ):
            return held[1]
        if finds(current.nodelist, self._is_self):
            placement = Placement.of(((current.nodelist, self._is_self),))
            placed[current] = (None, placement)
            return placement
        blocks = rendering_blocks(context)
        placement = Placement.of(self._path(current, blocks))
        placed[current] = (nodelists(blocks), placement)
        return placement

    def _path(
        self, current: Template | None, blocks: tuple[BlockNode, ...]
    ) -> tuple[tuple[NodeList, Callable[[Node], bool]], ...]:
        """Return each nodelist from the rendered template down to this call.

        A child block continues the text its parent puts before it, an include does not.
        """
        if not blocks or not finds(blocks[-1].nodelist, self._is_self):
            return ()
        path: list[tuple[NodeList, Callable[[Node], bool]]] = [
            (blocks[-1].nodelist, self._is_self)
        ]
        name = blocks[-1].name
        for block in reversed(blocks[:-1]):
            placing = block_named(name)
            if finds(block.nodelist, placing):
                path.append((block.nodelist, placing))
                name = block.name
        placing = block_named(name)
        if current is not None and finds(current.nodelist, placing):
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
        error_ui = ComponentErrorUI(self, context, attempt, path)
        token = current_attempt.set(attempt)
        try:
            return contain(
                self.guard(context, info, source),
                functools.partial(self.attempt, context, info, path, attempt),
                error_ui,
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
            Addressing.COMPONENT,
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
        collector = cast("StaticCollector | None", context.get(COLLECTOR_KEY))
        if collector is None or not ruled(info):
            _collect(info, collector)
            return self._composed(context, info, path, props)
        mark = collector.mark()
        try:
            _collect(info, collector)
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

    def file_context(
        self, context: template.Context, path: Path, props: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Return the call-site context a `loading.djx` or an `error.djx` reads.

        Disclosed props sit at the top level over it, as the component body reads them.
        """
        render_ctx = cast("dict[str, Any]", context.flatten())
        for key in _INTERNAL_CONTEXT_KEYS:
            render_ctx.pop(key, None)
        if props:
            render_ctx.update(props)
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
        decision = decide(info, context.get(REQUEST_KEY))
        if decision.denial is not None:
            return ""
        in_job = decision is PENDING
        props: dict[str, Any] | None = None
        with Capture():
            props = self._resolved_props(context)
        tag = self.wrapper(context, info)
        retry = None
        if props is not None and (addressable(info) or client_mode(mode)):
            retry = self.token_input(info, mode, props)
        renderer = DeferredRender(
            self, info, path, props, granted=not in_job, tag=tag, trip=retry
        )
        sink = deferral_sink(context)
        if props is None or sink is None or in_loading():
            return renderer.alone(context)
        stored = mode is DeferMode.INLINE or _capturing.get()
        delivered = DeferMode.INLINE if stored else DeferMode.STREAM
        call = DeferredCall(
            site=BoundarySite(
                Addressing.COMPONENT, info.name, delivered, self._origin(), self.index
            ),
            options=self._options(info, delivered),
            key=self._boundary_key(context),
            tag=tag,
            source=wrapper_source(info),
            renderer=renderer,
        )
        if delivered is DeferMode.INLINE:
            return sink.inline(context, call) + stash_loading(context, call)
        if not in_job:
            _collect(info, context.get(COLLECTOR_KEY))
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
            renderer = DeferredRender(self, info, path, None, granted=False, tag=tag)
            return renderer.alone(context)
        if not decide(info, context.get(REQUEST_KEY), props_lookup(props)).passed:
            return ""
        trip = self.token_input(info, mode, props)
        renderer = DeferredRender(
            self, info, path, props, granted=True, tag=tag, trip=trip
        )
        sink = deferral_sink(context)
        if sink is None:
            return renderer.alone(context)
        call = DeferredCall(
            site=BoundarySite(
                Addressing.COMPONENT, info.name, mode, self._origin(), self.index
            ),
            options=self._options(info, mode),
            key=self._boundary_key(context),
            tag=tag,
            source=wrapper_source(info),
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
        if self.has_slots or any(map(significant, self.nodelist)):
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
        return name if isinstance(name, str) else anchored(str(origin.name))

    def _options(self, info: ComponentInfo, mode: DeferMode) -> BoundaryOptions:
        """Return the options the call runs under, the call site over the module."""
        written = defer_declarations.options(info.module_path) or BoundaryOptions()
        call = self.options
        if call is None:
            return replace(written, mode=mode)
        timeout = written.timeout if call.timeout is None else call.timeout
        return replace(written, mode=mode, timeout=timeout)

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


def isolated_renderer(
    info: ComponentInfo, path: Path, trip: TripInput
) -> DeferredRender:
    """Return the renderer of one component from its props alone, as a reload runs it.

    Its access rule already passed, its call site is gone, so its declared tag wraps it.
    """
    node = ComponentNode(info.name, {}, NodeList())
    node.prop_names = frozenset(trip.props)
    options = defer_declarations.options(info.module_path)
    tag = options.tag if options is not None and options.tag_declared else DEFAULT_TAG
    return DeferredRender(
        node, info, path, dict(trip.props), granted=True, tag=tag, trip=trip
    )


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
        slot_content = context.get(f"{SLOT_KEY_PREFIX}{self.name}", Absent.SLOT)
        if slot_content is Absent.SLOT:
            return self.nodelist.render(context)
        return str(slot_content)


__all__ = ["ComponentNode", "SetSlotNode", "SlotNode", "isolated_renderer"]
