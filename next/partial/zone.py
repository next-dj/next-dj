"""Zone template tag, its node, and the standalone zone-body renderable."""

import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast, override

from django.template import Library, TemplateSyntaxError
from django.template.base import Node, NodeList, Template
from django.template.engine import Engine
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.text import unescape_string_literal

from next.components.stash import stash_markup, stashing
from next.defer import BoundaryOptionError, parse_duration
from next.pages.layouts import ZONE, seal_region
from next.pages.skeletons import render_skeleton, zone_skeleton
from next.seeding import TEMPLATE_PATH_KEY
from next.static.bundles import require_bundle
from next.static.runtime import RELOAD_CHUNK_KEY
from next.utils import child_nodelists


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from django.template.base import Origin, Parser, Token
    from django.template.context import Context


register = Library()

ZONE_ATTR = "data-next-zone"
POLL_ATTR = "data-next-poll"
RELOAD_ATTR = "data-next-reload"
DELAY_ATTR = "data-next-delay"

KEEP: Final = "keep"
LOADING: Final = "loading"
DEFAULT_DELAY_MS: Final = 150

_DEFAULT_TAG = "div"
_ZONE_NAME_INDEX = 1
_END_ZONE = ("endzone",)
_RELOADS: Final = (KEEP, LOADING)
_TAG_KWARG = "tag"
_POLL_KWARG = "poll"
_RELOAD_KWARG = "reload"
_DELAY_KWARG = "delay"
_STASHED: Final = "next.zone.stashed"
_KWARGS: Final = (_TAG_KWARG, _POLL_KWARG, _RELOAD_KWARG, _DELAY_KWARG)
_MIN_POLL_MS = 1000
_MAX_POLL_MS = 2_147_483_647
_MS_PER_SECOND = 1000
_POLL_DIGITS = re.compile(r"[0-9]+")
_GLOBAL_ATTRS: Final = frozenset(
    {
        "accesskey",
        "autocapitalize",
        "autocorrect",
        "autofocus",
        "class",
        "contenteditable",
        "dir",
        "draggable",
        "enterkeyhint",
        "hidden",
        "id",
        "inert",
        "inputmode",
        "is",
        "itemid",
        "itemprop",
        "itemref",
        "itemscope",
        "itemtype",
        "lang",
        "popover",
        "role",
        "slot",
        "spellcheck",
        "style",
        "tabindex",
        "title",
        "translate",
        "writingsuggestions",
    }
)
_PREFIXED_ATTR = re.compile(r"(?:aria|data)-[a-z0-9][a-z0-9._-]*")
_RESERVED_ATTR_PREFIX: Final = "data-next-"
_RESERVE_ATTR: Final = "data-next-reserve"
_OFF: Final = "off"
_AUTHOR_ATTRS: Final = frozenset(
    {
        "data-next-current",
        "data-next-focus",
        "data-next-match",
        "data-next-nav",
        "data-next-persist",
        "data-next-prefetch",
        "data-next-press",
        _RESERVE_ATTR,
        "data-next-router",
        "data-next-scroll",
        "data-next-scroll-region",
        "data-next-toasts",
    }
)
"""The hand-written `data-next-*` names the runtime reads on any element.

The runtime never writes them on a zone wrapper, and only reserve tunes a zone reload.
"""


def _literal(raw: str, subject: str) -> str:
    """Return the text of a quoted tag literal, refusing anything unquoted.

    An unquoted value is a template variable, whose name would silently replace it.
    """
    try:
        return unescape_string_literal(raw).strip()
    except ValueError as error:
        msg = (
            f"{{% zone %}} {subject} must be a quoted literal, got {raw!r}. "
            "Template variables are not read."
        )
        raise TemplateSyntaxError(msg) from error


def _wrap_zone(tag: str, name: str, body: str, *, extra: str = "") -> SafeString:
    """Return the addressable wrapper element around a rendered zone body.

    A non-empty `extra` carries its own leading space, so the plain wrapper has none.
    """
    return SafeString(f'<{tag} {ZONE_ATTR}="{name}"{extra}>{body}</{tag}>')


@dataclass(frozen=True, slots=True)
class ZoneOptions:
    """Compile-time rendering options of one zone.

    Both render paths derive wrapper attributes from it, so they cannot diverge.
    """

    tag: str = _DEFAULT_TAG
    poll: int | None = None
    reload: str = KEEP
    delay: int | None = None
    html: str = ""
    """The author's HTML attributes of the wrapper, escaped once at compile time."""

    @property
    def attrs(self) -> str:
        """Wrapper attributes of the zone, the defaults left unwritten."""
        attrs = "" if self.poll is None else f' {POLL_ATTR}="{self.poll}"'
        if self.reload == LOADING:
            attrs += f' {RELOAD_ATTR}="{LOADING}"'
        if self.delay is not None:
            attrs += f' {DELAY_ATTR}="{self.delay}"'
        return attrs + self.html


def render_zone_body(
    partial: "ZonePartial",
    name: str,
    options: ZoneOptions,
    context: "Context",
    *,
    page_template: Template | None = None,
    around: "Callable[[Callable[[], str]], str] | None" = None,
    stashed: bool = False,
) -> tuple[SafeString, SafeString]:
    """Render one zone body and its addressable wrapper element.

    Returned separately since append/prepend graft the bare body, morph the wrapper.
    An `around` call renders the body, as the page boundaries enclosing the zone do.
    """
    if options.reload == LOADING:
        return _render_loading_body(
            partial, name, options, context, page_template, around, stashed=stashed
        )
    if around is None:
        body = partial.render(context, page_template=page_template)
    else:
        render = functools.partial(partial.render, context, page_template=page_template)
        body = SafeString(around(render))
    return body, _wrap_zone(options.tag, name, body, extra=options.attrs)


def _render_loading_body(
    partial: "ZonePartial",
    name: str,
    options: ZoneOptions,
    context: "Context",
    page_template: Template | None,
    around: "Callable[[Callable[[], str]], str] | None",
    *,
    stashed: bool,
) -> tuple[SafeString, SafeString]:
    """Render a zone reloading to its loading UI, the inline boundary stashes inside.

    A `stashed` zone carries its own skeleton in its wrapper, which only a morph places.
    """
    render = functools.partial(partial.render, context, page_template=page_template)
    with stashing():
        body = render() if around is None else SafeString(around(render))
    require_bundle(context, RELOAD_CHUNK_KEY)
    inner = body + _skeleton_stash(name, context) if stashed else body
    return body, _wrap_zone(options.tag, name, inner, extra=options.attrs)


def _skeleton_stash(name: str, context: "Context") -> str:
    """Return the `loading.<zone>.djx` stash of `name`, once per zone and response."""
    seen = cast("dict[object, bool]", context.render_context.dicts[0])
    slot = (_STASHED, name)
    path = context.get(TEMPLATE_PATH_KEY)
    if slot in seen or not isinstance(path, str | Path):
        return ""
    skeleton = zone_skeleton(Path(path), name)
    if skeleton is None:
        return ""
    seen[slot] = True
    return stash_markup(name, render_skeleton(skeleton, context))


def render_zone_standalone(
    partial: "ZonePartial", name: str, options: ZoneOptions, context: "Context"
) -> SafeString:
    """Render one zone body wrapped in its addressable element."""
    _body, wrapped = render_zone_body(partial, name, options, context)
    return wrapped


class ZonePartial:
    """Standalone renderable for one zone body, shared by every thread rendering it.

    It keeps no per-render state, so a render's page template rides the render context.
    """

    def __init__(
        self, nodelist: NodeList, name: str, origin: "Origin | None", engine: Engine
    ) -> None:
        """Store the body node list and the template identity it stands for."""
        self.nodelist = nodelist
        self.name = name
        self.origin = origin
        self.engine = engine

    def render(
        self, context: "Context", *, page_template: Template | None = None
    ) -> SafeString:
        """Render the zone body inside its own template state.

        The page template, bound or passed, answers DEBUG for nested zones as well.
        """
        if isinstance(context.template, Template):
            page_template = context.template
        as_template = cast("Template", self)
        owner = as_template if page_template is None else page_template
        with context.render_context.push_state(owner):
            if context.template is None:
                with context.bind_template(owner):
                    context.template_name = self.name
                    return self.nodelist.render(context)
            return self.nodelist.render(context)

    def get_exception_info(
        self, _exception: Exception, _token: "Token"
    ) -> dict[str, object]:
        """Return no debug info, as a render without a page template has no source."""
        return {}


class ZoneNode(Node):
    """A named zone of a page template, always rendered inline."""

    child_nodelists = ("nodelist",)

    def __init__(
        self, name: str, partial: ZonePartial, *, options: ZoneOptions
    ) -> None:
        """Store the zone name, its body partial, and its rendering options."""
        self.name = name
        self.partial = partial
        self.nodelist = partial.nodelist
        self.options = options

    @override
    def render(self, context: "Context") -> SafeString:
        """Render the zone inline, its reload skeleton stashed after it once.

        Under the client router the zone sits between sealed comments.
        """
        wrapped = render_zone_standalone(self.partial, self.name, self.options, context)
        if self.options.reload == LOADING:
            wrapped = SafeString(wrapped + _skeleton_stash(self.name, context))
        return SafeString(seal_region(context, ZONE, wrapped))


type Ancestor = type[Node] | tuple[type[Node], ...]
"""A node class, or several, whose subtree a walk tells apart."""


def _zones_inside(
    nodelist: NodeList, ancestor: "Ancestor", *, inside: bool
) -> "Iterator[str]":
    """Yield the zone names of `nodelist`, all of them once `inside` an `ancestor`."""
    for node in nodelist:
        if inside and isinstance(node, ZoneNode):
            yield node.name
        now_inside = inside or isinstance(node, ancestor)
        for child in child_nodelists(node):
            yield from _zones_inside(child, ancestor, inside=now_inside)


def zones_under(nodelist: NodeList, ancestor: "Ancestor") -> "Iterator[str]":
    """Yield the name of every zone below an `ancestor` node, zone bodies included."""
    return _zones_inside(nodelist, ancestor, inside=False)


def _parse_options(token: "Token") -> tuple[str, ZoneOptions]:
    """Return the zone name and its parsed rendering options."""
    bits = token.split_contents()
    if len(bits) < _ZONE_NAME_INDEX + 1:
        msg = '{% zone %} tag requires a quoted zone name, e.g. {% zone "name" %}.'
        raise TemplateSyntaxError(msg)
    name = _literal(bits[_ZONE_NAME_INDEX], "name")
    if not name:
        msg = "{% zone %} tag requires a non-empty quoted zone name."
        raise TemplateSyntaxError(msg)
    values: dict[str, str] = {}
    html: dict[str, str] = {}
    for part in bits[_ZONE_NAME_INDEX + 1 :]:
        key, sep, raw = part.partition("=")
        if not sep:
            msg = (
                f"{{% zone %}} option {part!r} is missing '='. Write options "
                'as key="value" with no spaces around =.'
            )
            raise TemplateSyntaxError(msg)
        if key in _KWARGS:
            values[key] = _literal(raw, f"{key}=")
        else:
            html[_attribute_name(key)] = _attribute_value(key, raw)
    reload = _validate_reload(values.get(_RELOAD_KWARG, KEEP))
    delay = None
    if _DELAY_KWARG in values:
        if reload != LOADING:
            msg = f'{{% zone %}} delay= applies to reload="{LOADING}" only.'
            raise TemplateSyntaxError(msg)
        delay = _validate_delay(values[_DELAY_KWARG])
    poll = values.get(_POLL_KWARG)
    options = ZoneOptions(
        tag=values.get(_TAG_KWARG) or _DEFAULT_TAG,
        poll=None if poll is None else _validate_poll(poll),
        reload=reload,
        delay=delay,
        html="".join(format_html(' {}="{}"', key, raw) for key, raw in html.items()),
    )
    return name, options


def _attribute_name(key: str) -> str:
    """Return `key` when it names an HTML global, `aria-*` or `data-*` attribute.

    The runtime owns the other `data-next-*` names, and an unknown one is a misspelling.
    """
    if key.startswith(_RESERVED_ATTR_PREFIX) and key not in _AUTHOR_ATTRS:
        msg = f"{{% zone %}} reserves the {key!r} attribute for the framework."
        raise TemplateSyntaxError(msg)
    if key in _GLOBAL_ATTRS or _PREFIXED_ATTR.fullmatch(key):
        return key
    msg = (
        f"{{% zone %}} got an unknown option {key!r}. The valid options are tag, "
        "poll, reload, and delay, plus the HTML global, aria-* and data-* attributes "
        "of the wrapper."
    )
    raise TemplateSyntaxError(msg)


def _attribute_value(key: str, raw: str) -> str:
    """Return the literal value of a wrapper attribute, reserve accepting off alone."""
    value = _literal(raw, f"{key}=")
    if key == _RESERVE_ATTR and value != _OFF:
        msg = f"{{% zone %}} {_RESERVE_ATTR} must be {_OFF}, got {value!r}."
        raise TemplateSyntaxError(msg)
    return value


def _validate_reload(value: str) -> str:
    """Return a validated reload mode or raise on an unknown value."""
    if value not in _RELOADS:
        modes = ", ".join(_RELOADS)
        msg = f"{{% zone %}} reload must be one of {modes}, got {value!r}."
        raise TemplateSyntaxError(msg)
    return value


def _validate_delay(value: str) -> int | None:
    """Return the delay in ms, `None` for the default one, from a duration literal."""
    try:
        seconds = parse_duration(value)
    except BoundaryOptionError as error:
        msg = f"{{% zone %}} delay= refuses its value, {error}."
        raise TemplateSyntaxError(msg) from error
    ms = round(seconds * _MS_PER_SECOND)
    return None if ms == DEFAULT_DELAY_MS else ms


def _validate_poll(value: str) -> int:
    """Return the poll interval in ms parsed from a 5s or 1500ms literal.

    A bare number is milliseconds in ASCII digits, out-of-range values fail to compile.
    """
    if value.endswith("ms"):
        digits, scale = value[:-2], 1
    elif value.endswith("s"):
        digits, scale = value[:-1], _MS_PER_SECOND
    else:
        digits, scale = value, 1
    if _POLL_DIGITS.fullmatch(digits) is None:
        msg = (
            "{% zone %} poll must be a quoted literal duration like 5s or "
            f"1500ms, got {value!r}. Template variables are not read."
        )
        raise TemplateSyntaxError(msg)
    ms = int(digits) * scale
    if ms < _MIN_POLL_MS:
        msg = f"{{% zone %}} poll must be at least {_MIN_POLL_MS}ms, got {ms}ms."
        raise TemplateSyntaxError(msg)
    if ms > _MAX_POLL_MS:
        msg = f"{{% zone %}} poll must be at most {_MAX_POLL_MS}ms, got {ms}ms."
        raise TemplateSyntaxError(msg)
    return ms


@register.tag(name="zone")
def do_zone(parser: "Parser", token: "Token") -> ZoneNode:
    """Compile `{% zone "name" tag=... poll=... reload=... %}` … `{% endzone %}`.

    Registers nothing with the zone registry, derived from the compiled page template.
    """
    name, options = _parse_options(token)
    body = parser.parse(_END_ZONE)
    parser.delete_first_token()
    partial = ZonePartial(
        nodelist=body, name=name, origin=parser.origin, engine=Engine.get_default()
    )
    return ZoneNode(name=name, partial=partial, options=options)


__all__ = [
    "DEFAULT_DELAY_MS",
    "DELAY_ATTR",
    "KEEP",
    "LOADING",
    "POLL_ATTR",
    "RELOAD_ATTR",
    "ZONE_ATTR",
    "Ancestor",
    "ZoneNode",
    "ZoneOptions",
    "ZonePartial",
    "do_zone",
    "register",
    "render_zone_body",
    "render_zone_standalone",
    "zones_under",
]
