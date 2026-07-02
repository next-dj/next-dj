"""Zone template tag, its node, and the standalone zone-body renderable."""

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
_MS_PER_SECOND = 1000


def _strip_quotes(raw: str) -> str:
    """Return a quoted tag literal as a bare string."""
    return raw.strip("'\"").strip()


def _wrap_zone(tag: str, name: str, body: str) -> SafeString:
    """Return the addressable wrapper element around a rendered zone body."""
    return SafeString(f'<{tag} {ZONE_ATTR}="{name}">{body}</{tag}>')


def render_zone_standalone(
    partial: "ZonePartial",
    name: str,
    tag: str,
    context: "Context",
) -> SafeString:
    """Render one zone body wrapped in its addressable element.

    The wrapper drops the lazy hint because a partial request asks for
    the body by name and the body has already arrived. Both the inline
    full render and the partial path share this single wrapper string.
    """
    return _wrap_zone(tag, name, partial.render(context))


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
        tag: str = _DEFAULT_TAG,
        lazy: str | None = None,
        poll: int | None = None,
        placeholder: NodeList | None = None,
    ) -> None:
        """Store the zone name, its body partial, and its rendering options."""
        self.name = name
        self.partial = partial
        self.nodelist = partial.nodelist
        self.tag = tag
        self.lazy = lazy
        self.poll = poll
        self.placeholder = placeholder if placeholder is not None else NodeList()

    @override
    def render(self, context: "Context") -> SafeString:
        """Render the zone inline on a full page render."""
        if self.lazy is not None:
            open_tag = (
                f'<{self.tag} {ZONE_ATTR}="{self.name}" {LAZY_ATTR}="{self.lazy}">'
            )
            return SafeString(
                f"{open_tag}{self.placeholder.render(context)}</{self.tag}>"
            )
        body = self.partial.render(context)
        if self.poll is None:
            return _wrap_zone(self.tag, self.name, body)
        open_tag = f'<{self.tag} {ZONE_ATTR}="{self.name}" {POLL_ATTR}="{self.poll}">'
        return SafeString(f"{open_tag}{body}</{self.tag}>")


def _parse_options(token: "Token") -> tuple[str, str, str | None, int | None]:
    """Return the zone name, wrapper tag, lazy trigger, and poll interval."""
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
            continue
        value = _strip_quotes(raw)
        if key == _TAG_KWARG:
            tag = value or _DEFAULT_TAG
        elif key == _LAZY_KWARG:
            lazy = _validate_lazy(value)
        elif key == _POLL_KWARG:
            poll = _validate_poll(value)
    if lazy is not None and poll is not None:
        msg = "{% zone %} cannot combine poll= with lazy=, the modes are exclusive."
        raise TemplateSyntaxError(msg)
    return name, tag, lazy, poll


def _validate_lazy(value: str) -> str:
    """Return a validated lazy trigger or raise on an unknown value."""
    if value not in _LAZY_TRIGGERS:
        triggers = ", ".join(sorted(_LAZY_TRIGGERS))
        msg = f"{{% zone %}} lazy must be one of {triggers}, got {value!r}."
        raise TemplateSyntaxError(msg)
    return value


def _validate_poll(value: str) -> int:
    """Return the poll interval in ms parsed from a 5s or 500ms literal.

    A bare number is read as milliseconds. An interval below the floor or a
    malformed literal fails at compile time, the same honest-fail as lazy.
    """
    text = value.strip()
    try:
        if text.endswith("ms"):
            ms = int(text[:-2])
        elif text.endswith("s"):
            ms = int(text[:-1]) * _MS_PER_SECOND
        else:
            ms = int(text)
    except ValueError:
        msg = f"{{% zone %}} poll must be a duration like 5s or 500ms, got {value!r}."
        raise TemplateSyntaxError(msg) from None
    if ms < _MIN_POLL_MS:
        msg = f"{{% zone %}} poll must be at least {_MIN_POLL_MS}ms, got {ms}ms."
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
    name, tag, lazy, poll = _parse_options(token)
    body = parser.parse(_PLACEHOLDER_THEN_END)
    placeholder: NodeList | None = None
    if parser.next_token().contents.split()[0] == "placeholder":
        placeholder = parser.parse(_END_ZONE)
        parser.delete_first_token()
    partial = ZonePartial(
        nodelist=body,
        name=name,
        origin=parser.origin,
        engine=Engine.get_default(),
    )
    return ZoneNode(
        name=name,
        partial=partial,
        tag=tag,
        lazy=lazy,
        poll=poll,
        placeholder=placeholder,
    )


__all__ = [
    "LAZY_ATTR",
    "POLL_ATTR",
    "ZONE_ATTR",
    "ZoneNode",
    "ZonePartial",
    "do_zone",
    "register",
    "render_zone_standalone",
]
