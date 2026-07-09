"""Zone template tag, its node, and the standalone zone-body renderable."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast, override

from django.template import Library, TemplateSyntaxError
from django.template.base import Node, NodeList, Template
from django.template.engine import Engine
from django.utils.safestring import SafeString


if TYPE_CHECKING:
    from django.template.base import Origin, Parser, Token
    from django.template.context import Context


register = Library()

ZONE_ATTR = "data-next-zone"
LAZY_ATTR = "data-next-lazy"
POLL_ATTR = "data-next-poll"

_DEFAULT_TAG = "div"
_ZONE_NAME_INDEX = 1
_END_ZONE = ("endzone",)
_PLACEHOLDER_THEN_END = ("placeholder", "endzone")
_LAZY_TRIGGERS = frozenset({"load", "revealed"})
_TAG_KWARG = "tag"
_LAZY_KWARG = "lazy"
_POLL_KWARG = "poll"
_MIN_POLL_MS = 1000
_MAX_POLL_MS = 2_147_483_647
_MS_PER_SECOND = 1000
_POLL_DIGITS = re.compile(r"[0-9]+")


def _strip_quotes(raw: str) -> str:
    """Return a quoted tag literal as a bare string."""
    return raw.strip("'\"").strip()


def _wrap_zone(tag: str, name: str, body: str, *, extra: str = "") -> SafeString:
    """Return the addressable wrapper element around a rendered zone body.

    A non-empty `extra` carries its own leading space, so the plain
    wrapper stays byte-for-byte free of trailing whitespace.
    """
    return SafeString(f'<{tag} {ZONE_ATTR}="{name}"{extra}>{body}</{tag}>')


@dataclass(frozen=True, slots=True)
class ZoneOptions:
    """Compile-time rendering options of one zone.

    Both render paths derive their wrapper attributes from these options,
    so a new mode cannot diverge the full render from the standalone
    delivery.
    """

    tag: str = _DEFAULT_TAG
    lazy: str | None = None
    poll: int | None = None

    def __post_init__(self) -> None:
        """Reject the exclusive lazy and poll modes appearing together."""
        if self.lazy is not None and self.poll is not None:
            msg = "ZoneOptions cannot combine poll with lazy, the modes are exclusive."
            raise ValueError(msg)

    @property
    def delivery_attrs(self) -> str:
        """Wrapper attributes of a delivered zone body, without the lazy hint."""
        return "" if self.poll is None else f' {POLL_ATTR}="{self.poll}"'

    @property
    def full_attrs(self) -> str:
        """Wrapper attributes of the zone on a full page render."""
        if self.lazy is None:
            return self.delivery_attrs
        return f' {LAZY_ATTR}="{self.lazy}"'


def render_zone_body(
    partial: "ZonePartial",
    name: str,
    options: ZoneOptions,
    context: "Context",
) -> tuple[SafeString, SafeString]:
    """Render one zone body and its addressable wrapper element.

    The first element is the bare inner body, the second wraps it in the
    marker element carrying the delivery attributes of the zone options.
    An append or prepend merge grafts the bare body into the live zone, so
    it needs the body without the wrapper the morph path addresses.
    """
    body = partial.render(context)
    wrapped = _wrap_zone(options.tag, name, body, extra=options.delivery_attrs)
    return body, wrapped


def render_zone_standalone(
    partial: "ZonePartial",
    name: str,
    options: ZoneOptions,
    context: "Context",
) -> SafeString:
    """Render one zone body wrapped in its addressable element.

    The wrapper carries the delivery attributes of the zone options,
    which drop the lazy hint because the body has already arrived.
    """
    _body, wrapped = render_zone_body(partial, name, options, context)
    return wrapped


class ZonePartial:
    """Standalone renderable for one zone body that owns its template state.

    The body renders inside its own render-context state so a partial
    request can render the zone alone with the full page context. The
    object stands in for the page template on the render-context stack,
    so it answers `get_exception_info` by delegating to the page template
    and DEBUG tracebacks stay honest.
    """

    def __init__(
        self,
        nodelist: NodeList,
        name: str,
        origin: "Origin | None",
        engine: Engine,
    ) -> None:
        """Store the body node list and the template identity it stands for."""
        self.nodelist = nodelist
        self.name = name
        self.origin = origin
        self.engine = engine
        self.page_template: Template | None = None

    def render(self, context: "Context") -> SafeString:
        """Render the zone body inside its own template state."""
        if isinstance(context.template, Template):
            self.page_template = context.template
        as_template = cast("Template", self)
        with context.render_context.push_state(as_template):
            if context.template is None:
                with context.bind_template(as_template):
                    context.template_name = self.name
                    return self.nodelist.render(context)
            return self.nodelist.render(context)

    def get_exception_info(
        self, exception: Exception, token: "Token"
    ) -> dict[str, object]:
        """Delegate debug info to the page template so tracebacks stay honest."""
        if self.page_template is None:
            return {}
        return self.page_template.get_exception_info(exception, token)


class ZoneNode(Node):
    """A named zone of a page template, rendered inline or as a placeholder.

    On a full render a non-lazy zone wraps its body in a marker element
    so the client can address it by name. A lazy zone renders only its
    placeholder branch, its body arrives later as a patch.
    """

    child_nodelists = ("nodelist", "placeholder")

    def __init__(
        self,
        name: str,
        partial: ZonePartial,
        *,
        options: ZoneOptions,
        placeholder: NodeList | None = None,
    ) -> None:
        """Store the zone name, its body partial, and its rendering options."""
        self.name = name
        self.partial = partial
        self.nodelist = partial.nodelist
        self.options = options
        self.placeholder = placeholder if placeholder is not None else NodeList()

    @override
    def render(self, context: "Context") -> SafeString:
        """Render the zone inline on a full page render."""
        options = self.options
        if options.lazy is not None:
            return _wrap_zone(
                options.tag,
                self.name,
                self.placeholder.render(context),
                extra=options.full_attrs,
            )
        return render_zone_standalone(self.partial, self.name, options, context)


def _parse_options(token: "Token") -> tuple[str, ZoneOptions]:
    """Return the zone name and its parsed rendering options."""
    bits = token.split_contents()
    if len(bits) < _ZONE_NAME_INDEX + 1:
        msg = '{% zone %} tag requires a quoted zone name, e.g. {% zone "name" %}.'
        raise TemplateSyntaxError(msg)
    name = _strip_quotes(bits[_ZONE_NAME_INDEX])
    if not name:
        msg = "{% zone %} tag requires a non-empty quoted zone name."
        raise TemplateSyntaxError(msg)
    tag = _DEFAULT_TAG
    lazy: str | None = None
    poll: int | None = None
    for part in bits[_ZONE_NAME_INDEX + 1 :]:
        key, sep, raw = part.partition("=")
        if not sep:
            msg = (
                f"{{% zone %}} option {part!r} is missing '='. Write options "
                'as key="value" with no spaces around =.'
            )
            raise TemplateSyntaxError(msg)
        value = _strip_quotes(raw)
        if key == _TAG_KWARG:
            tag = value or _DEFAULT_TAG
        elif key == _LAZY_KWARG:
            lazy = _validate_lazy(value)
        elif key == _POLL_KWARG:
            poll = _validate_poll(value)
        else:
            msg = (
                f"{{% zone %}} got an unknown option {key!r}. The valid "
                "options are tag, lazy, and poll."
            )
            raise TemplateSyntaxError(msg)
    try:
        options = ZoneOptions(tag=tag, lazy=lazy, poll=poll)
    except ValueError as error:
        msg = "{% zone %} cannot combine poll= with lazy=, the modes are exclusive."
        raise TemplateSyntaxError(msg) from error
    return name, options


def _validate_lazy(value: str) -> str:
    """Return a validated lazy trigger or raise on an unknown value."""
    if value not in _LAZY_TRIGGERS:
        triggers = ", ".join(sorted(_LAZY_TRIGGERS))
        msg = f"{{% zone %}} lazy must be one of {triggers}, got {value!r}."
        raise TemplateSyntaxError(msg)
    return value


def _validate_poll(value: str) -> int:
    """Return the poll interval in ms parsed from a 5s or 1500ms literal.

    A bare number is read as milliseconds and the digits must be plain
    ASCII. An interval outside the floor-to-ceiling range or a malformed
    literal fails at compile time, the same honest-fail as lazy.
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
    """Compile `{% zone "name" tag=... lazy=... poll=... %}` … `{% endzone %}`.

    The body compiles into a standalone `ZonePartial`. An optional
    `{% placeholder %}` branch holds the markup shown until a lazy body
    arrives. This hook registers nothing with the zone registry, the
    registry is derived from the compiled page template on demand.
    """
    name, options = _parse_options(token)
    body = parser.parse(_PLACEHOLDER_THEN_END)
    placeholder: NodeList | None = None
    if parser.next_token().contents.split()[0] == "placeholder":
        placeholder = parser.parse(_END_ZONE)
        parser.delete_first_token()
    if placeholder is not None and options.lazy is None:
        msg = (
            f'{{% zone "{name}" %}} placeholder requires lazy=, a zone that '
            "shows its body never renders the placeholder branch."
        )
        raise TemplateSyntaxError(msg)
    partial = ZonePartial(
        nodelist=body,
        name=name,
        origin=parser.origin,
        engine=Engine.get_default(),
    )
    return ZoneNode(
        name=name,
        partial=partial,
        options=options,
        placeholder=placeholder,
    )


__all__ = [
    "LAZY_ATTR",
    "POLL_ATTR",
    "ZONE_ATTR",
    "ZoneNode",
    "ZoneOptions",
    "ZonePartial",
    "do_zone",
    "register",
    "render_zone_body",
    "render_zone_standalone",
]
