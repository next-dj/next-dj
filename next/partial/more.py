"""The `{% more %}` infinite-list tag and the filters that adapt a Django page to it."""

import enum
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast, override

from django.core.paginator import Page
from django.http import HttpRequest, QueryDict
from django.template import TemplateSyntaxError
from django.template.base import Node, NodeList
from django.template.engine import Engine
from django.utils.html import escape
from django.utils.safestring import SafeString
from django.utils.text import unescape_string_literal

from next.conf.settings import fail_loudly
from next.seeding import REQUEST_KEY
from next.static.collector import require_bundle
from next.static.runtime import MORE_CHUNK_KEY

from .headers import MergeMode
from .keys import KEY_ATTR, TARGET_ATTR
from .paging import (
    _DIGITS,
    _SLUG,
    DEFAULT_PARAM,
    MAX_TOKEN,
    RESTORE_PARAM,
    Window,
    valid_param,
)
from .zone import ZoneNode, ZoneOptions, ZonePartial


if TYPE_CHECKING:
    from django.template.base import FilterExpression, Origin, Parser, Token
    from django.template.context import Context


MORE_SUFFIX: Final = "-more"
LESS_SUFFIX: Final = "-less"

MERGE_ATTR: Final = "data-next-merge"
MARGIN_ATTR: Final = "data-next-margin"
DEFER_ATTR: Final = "data-next-defer"
PARAM_ATTR: Final = "data-next-param"
PRUNE_ATTR: Final = "data-next-prune"
URL_ATTR: Final = "data-next-url"
BACK_ATTR: Final = "data-next-back"
BACKS_ATTR: Final = "data-next-backs"

_TAG: Final = "{% more %}"
_EMPTY: Final = "empty"
_END: Final = ("endmore",)
_EMPTY_OR_END: Final = (_EMPTY, "endmore")
_NEXT: Final = "next"
_PREVIOUS: Final = "previous"
_PRUNE: Final = "prune"
_URL: Final = "url"
_OPTIONS: Final = (
    "zone",
    _NEXT,
    _PREVIOUS,
    "mode",
    "merge",
    "margin",
    "param",
    _PRUNE,
    _URL,
)
_HIDDEN: Final = " hidden"
_MAX_DIGITS: Final = 18

_NUMBER: Final = re.compile(rf"[0-9]{{1,{_MAX_DIGITS}}}")
_PIXELS: Final = re.compile(r"[0-9]{1,6}")
_LENGTH: Final = r"-?(?:[0-9]{1,6}|[0-9]{0,6}\.[0-9]{1,4})(?:px|%)"
_MARGIN: Final = re.compile(rf"{_LENGTH}(?: {_LENGTH}){{0,3}}")


class MoreMode(enum.StrEnum):
    """When a `{% more %}` link fetches its batch, on sight or on a click."""

    VISIBLE = "visible"
    MANUAL = "manual"


class UrlMode(enum.StrEnum):
    """How a `{% more %}` link writes the window it loaded into the address bar."""

    REPLACE = "replace"
    PUSH = "push"


def _raw_token(value: object) -> str | None:
    """Return the token a value other than a window names, `None` when it names none.

    A page number counts from 1, digits read as one, and any other slug passes as is.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value >= 1 else None
    if not isinstance(value, str) or len(value) > MAX_TOKEN:
        return None
    if _DIGITS.fullmatch(value) is not None:
        number = int(value) if _NUMBER.fullmatch(value) is not None else 0
        return str(number) if number >= 1 else None
    return value if _SLUG.fullmatch(value) is not None else None


def next_page(value: object) -> int | None:
    """Return the page after a Django `Page`, `None` for the last page or a non-page."""
    if isinstance(value, Page) and value.has_next():
        return value.next_page_number()
    return None


def previous_page(value: object) -> int | None:
    """Return the page before a Django `Page`, `None` for page 1 or anything else."""
    if isinstance(value, Page) and value.has_previous():
        return value.previous_page_number()
    return None


def page_href(request: HttpRequest | None, param: str, token: str | None) -> str:
    """Return the escaped `?query` of the request with `param` set to `token`.

    The restore flag is dropped, and a `None` token drops `param` for the first window.
    """
    query = QueryDict(mutable=True) if request is None else request.GET.copy()
    query.pop(RESTORE_PARAM, None)
    if token is None:
        query.pop(param, None)
    else:
        query[param] = token
    return escape(f"?{query.urlencode()}")


@dataclass(frozen=True, slots=True)
class MoreOptions:
    """Compile-time options of one `{% more %}` tag.

    A `previous=` tag grows the list from above, through the `<zone>-less` zone.
    """

    zone: str
    previous: bool = False
    mode: MoreMode = MoreMode.VISIBLE
    merge: MergeMode = MergeMode.APPEND
    margin: str | None = None
    param: str | None = None
    prune: int | None = None
    url: UrlMode | None = None

    @property
    def windowed(self) -> bool:
        """Whether the tag prunes rows or syncs the URL, both served by a restore."""
        return self.prune is not None or self.url is not None

    @property
    def sentinel(self) -> str:
        """Name of the zone that holds the link."""
        suffix = LESS_SUFFIX if self.previous else MORE_SUFFIX
        return f"{self.zone}{suffix}"

    @property
    def head(self) -> str:
        """Return the sentinel attributes every link of the tag carries."""
        key = self.sentinel
        attrs = (
            f' {KEY_ATTR}="{key}" {TARGET_ATTR}="{self.zone},{key}" '
            f'{MERGE_ATTR}="{self.merge}"'
        )
        if self.mode is MoreMode.VISIBLE:
            attrs += f' {DEFER_ATTR}="{MoreMode.VISIBLE}"'
        if self.margin is not None:
            attrs += f' {MARGIN_ATTR}="{self.margin}"'
        return attrs

    @property
    def pruning(self) -> str:
        """Return the prune attribute of the link, empty without `prune=`."""
        return "" if self.prune is None else f' {PRUNE_ATTR}="{self.prune}"'

    @property
    def syncing(self) -> str:
        """Return the URL attribute of the link, empty without `url=`."""
        return "" if self.url is None else f' {URL_ATTR}="{self.url}"'


class MoreLinkNode(Node):
    """The body of the sentinel zone, the link to the batch or the end branch.

    A value that names no batch and is not `None` raises where loading fails loudly.
    """

    child_nodelists = ("label", "empty")

    def __init__(
        self,
        options: MoreOptions,
        target: "FilterExpression",
        label: NodeList,
        empty: NodeList,
    ) -> None:
        """Store the options, the expression and both branches."""
        self.options = options
        self.target = target
        self.label = label
        self.empty = empty
        self.head = options.head
        self.tail = options.pruning + options.syncing

    @override
    def render(self, context: "Context") -> SafeString:
        """Render the link to the batch, or the end branch when there is none.

        A top end branch also holds the link hidden, which the runtime shows on a prune.
        """
        value = self.target.resolve(context, ignore_failures=True)
        if isinstance(value, Window):
            return self._window(context, value)
        options = self.options
        param = options.param or DEFAULT_PARAM
        token = _raw_token(value)
        if token is None:
            if value is not None and fail_loudly():
                option = _PREVIOUS if options.previous else _NEXT
                msg = (
                    f"{option}= names no window, got {value!r}. Pass a Window, a page "
                    "number from 1, a base64url token, or None for the end of the "
                    f"list, as the {option}_page filter returns."
                )
                raise _fail(msg)
            return self._end(context, param)
        if options.prune is not None and fail_loudly():
            msg = (
                f"prune= needs a Window, got {value!r}. A raw token carries no back "
                "token, so a prune would leave the top link one window off. Pass the "
                "Window a pager returns."
            )
            raise _fail(msg)
        tail = options.syncing
        if tail:
            require_bundle(context, MORE_CHUNK_KEY)
        return SafeString(self._link(context, token, param, tail))

    def _window(self, context: "Context", window: Window[object]) -> SafeString:
        """Render the link a window names, with the back tokens a prune reads."""
        options = self.options
        param = window.param
        if options.param not in {None, param} and fail_loudly():
            msg = (
                f'param="{options.param}" differs from the param {param!r} of the '
                "window. Drop param=, as a window names its own."
            )
            raise _fail(msg)
        token = window.previous if options.previous else window.next
        if token is None:
            return self._end(context, param)
        tail = self.tail
        if tail:
            require_bundle(context, MORE_CHUNK_KEY)
            if window.back is not None:
                tail += f' {BACK_ATTR}="{escape(window.back)}"'
            if window.backs:
                tail += f' {BACKS_ATTR}="{escape(" ".join(window.backs))}"'
        return SafeString(self._link(context, token, param, tail))

    def _end(self, context: "Context", param: str) -> SafeString:
        """Render the end branch, a top one with a hidden link to the first window."""
        body = self.empty.render(context)
        top = self._link(context, None, param, _HIDDEN) if self.options.previous else ""
        key = self.options.sentinel
        return SafeString(f'<div {KEY_ATTR}="{key}">{body}{top}</div>')

    def _link(
        self, context: "Context", token: str | None, param: str, tail: str
    ) -> str:
        """Return the link to the batch `token` fetches with the sentinel attributes."""
        request = cast("HttpRequest | None", context.get(REQUEST_KEY))
        href = page_href(request, param, token)
        label = self.label.render(context)
        named = "" if param == DEFAULT_PARAM else f' {PARAM_ATTR}="{escape(param)}"'
        return f'<a href="{href}"{self.head}{named}{tail}>{label}</a>'


class MoreNode(Node):
    """A `{% more %}` tag holding its sentinel zone, a real zone node.

    The zone sits in a child node list, so the zone registry finds it.
    """

    def __init__(self, options: MoreOptions, zone: ZoneNode) -> None:
        """Store the options and the sentinel zone."""
        self.options = options
        self.nodelist = NodeList([zone])

    @override
    def render(self, context: "Context") -> SafeString:
        """Render the sentinel zone."""
        return SafeString(self.nodelist.render(context))


def _fail(message: str) -> TemplateSyntaxError:
    """Return the template error of the tag with its standard prefix."""
    return TemplateSyntaxError(f"{_TAG} {message}")


def _literal(raw: str, key: str) -> str:
    """Return the text of a quoted option literal, refusing a template variable."""
    try:
        return unescape_string_literal(raw).strip()
    except ValueError as error:
        msg = (
            f"{key}= must be a quoted literal, got {raw!r}. Template variables are "
            "not read."
        )
        raise _fail(msg) from error


def _numeral(raw: str, key: str) -> str:
    """Return the digits of a bare or quoted numeric option."""
    return raw if _DIGITS.fullmatch(raw) is not None else _literal(raw, key)


def _choice[E: enum.StrEnum](raw: str, key: str, kind: type[E]) -> E:
    """Return a quoted option value from the closed set of `kind`."""
    value = _literal(raw, key)
    try:
        return kind(value)
    except ValueError:
        allowed = ", ".join(member.value for member in kind)
        msg = f"{key}= must be one of {allowed}, got {value!r}."
        raise _fail(msg) from None


def _slug(raw: str, key: str) -> str:
    """Return a quoted option value that is an ASCII slug."""
    value = _literal(raw, key)
    if _SLUG.fullmatch(value) is None:
        msg = (
            f"{key}= must be an ASCII slug of letters, digits, - and _, got {value!r}."
        )
        raise _fail(msg)
    return value


def _param(raw: str) -> str:
    """Return the query parameter that carries the token of a raw value."""
    value = _literal(raw, "param")
    if not valid_param(value):
        msg = (
            f'param= must be an ASCII slug other than "{RESTORE_PARAM}", which flags '
            f"a restored window, got {value!r}."
        )
        raise _fail(msg)
    return value


def _margin(raw: str) -> str:
    """Return the observer margin, a bare number read as pixels."""
    value = _numeral(raw, "margin")
    if _PIXELS.fullmatch(value) is not None:
        return f"{int(value)}px"
    spaced = " ".join(value.split())
    if _MARGIN.fullmatch(spaced) is None:
        shown = value if len(value) <= _MAX_DIGITS else f"{value[:_MAX_DIGITS]}..."
        msg = (
            "margin= must be one to four px or % lengths of up to six digits like "
            f"400px, got {shown!r}."
        )
        raise _fail(msg)
    return spaced


def _prune(raw: str) -> int:
    """Return how many rows the list keeps before it drops whole batches on top."""
    value = _numeral(raw, _PRUNE)
    if _NUMBER.fullmatch(value) is None or int(value) < 1:
        shown = value if len(value) <= _MAX_DIGITS else f"{value[:_MAX_DIGITS]}..."
        msg = f"{_PRUNE}= must be a whole number of rows from 1, got {shown!r}."
        raise _fail(msg)
    return int(value)


def _read_bits(token: "Token") -> dict[str, str]:
    """Return the raw value of every option of the tag, refusing a malformed one."""
    found: dict[str, str] = {}
    for part in token.split_contents()[1:]:
        key, sep, raw = part.partition("=")
        if not sep or not raw:
            msg = f"option {part!r} needs a value. Write options as key=value."
            raise _fail(msg)
        if key not in _OPTIONS:
            valid = ", ".join(_OPTIONS)
            msg = f"got an unknown option {key!r}. The valid options are {valid}."
            raise _fail(msg)
        if key in found:
            msg = f"got the option {key!r} twice."
            raise _fail(msg)
        found[key] = raw
    if "zone" not in found or (_NEXT in found) == (_PREVIOUS in found):
        msg = (
            'requires zone= and one of next= or previous=, as in {% more zone="list" '
            "next=rows %}."
        )
        raise _fail(msg)
    return found


def _options(found: dict[str, str]) -> MoreOptions:
    """Return the validated options, a `previous=` tag prepending on a click."""
    previous = _PREVIOUS in found
    default_mode, default_merge = (
        (MoreMode.MANUAL, MergeMode.PREPEND)
        if previous
        else (MoreMode.VISIBLE, MergeMode.APPEND)
    )
    mode = _choice(found["mode"], "mode", MoreMode) if "mode" in found else default_mode
    margin = _margin(found["margin"]) if "margin" in found else None
    if margin is not None and mode is not MoreMode.VISIBLE:
        msg = 'margin= applies to mode="visible" only, a manual link is never observed.'
        raise _fail(msg)
    if previous and (_PRUNE in found or _URL in found):
        msg = "prune= and url= apply to a next= tag only, the list grows from below."
        raise _fail(msg)
    return MoreOptions(
        zone=_slug(found["zone"], "zone"),
        previous=previous,
        mode=mode,
        merge=_choice(found["merge"], "merge", MergeMode)
        if "merge" in found
        else default_merge,
        margin=margin,
        param=_param(found["param"]) if "param" in found else None,
        prune=_prune(found[_PRUNE]) if _PRUNE in found else None,
        url=_choice(found[_URL], _URL, UrlMode) if _URL in found else None,
    )


def _zone(name: str, body: Node, parser: "Parser", token: "Token") -> ZoneNode:
    """Return a zone node holding `body`, located at the tag for debug reports."""
    origin = cast("Origin", parser.origin)
    body.token = token
    body.origin = origin
    partial = ZonePartial(
        nodelist=NodeList([body]),
        name=name,
        origin=parser.origin,
        engine=Engine.get_default(),
    )
    node = ZoneNode(name=name, partial=partial, options=ZoneOptions())
    node.token = token
    node.origin = origin
    return node


def do_more(parser: "Parser", token: "Token") -> MoreNode:
    """Compile `{% more zone=... next=... %}label{% empty %}end{% endmore %}`.

    `previous=` in place of `next=` declares the link above the list instead.
    """
    found = _read_bits(token)
    options = _options(found)
    target = parser.compile_filter(found[_PREVIOUS if options.previous else _NEXT])
    label = parser.parse(_EMPTY_OR_END)
    empty = NodeList()
    if parser.next_token().contents.split()[0] == _EMPTY:
        empty = parser.parse(_END)
        parser.delete_first_token()
    link = MoreLinkNode(options, target, label, empty)
    return MoreNode(options, _zone(options.sentinel, link, parser, token))


__all__ = [
    "BACKS_ATTR",
    "BACK_ATTR",
    "DEFER_ATTR",
    "LESS_SUFFIX",
    "MARGIN_ATTR",
    "MERGE_ATTR",
    "MORE_SUFFIX",
    "PARAM_ATTR",
    "PRUNE_ATTR",
    "URL_ATTR",
    "MoreLinkNode",
    "MoreMode",
    "MoreNode",
    "MoreOptions",
    "UrlMode",
    "do_more",
    "next_page",
    "page_href",
    "previous_page",
]
