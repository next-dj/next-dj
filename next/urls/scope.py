"""The page routes of the URLconf as the table the client router matches a link by.

Each row is a regex over the path past the script prefix, a page or a route that is no
page, in resolution order. The router owns a link only when a page row matches it first.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.http import HttpResponse
from django.urls import (
    LocalePrefixPattern,
    NoReverseMatch,
    URLPattern,
    URLResolver,
    get_resolver,
    get_script_prefix,
    get_urlconf,
    reverse,
)
from django.urls.converters import (
    IntConverter,
    PathConverter,
    SlugConverter,
    StringConverter,
    UUIDConverter,
)
from django.urls.resolvers import RoutePattern
from django.utils.cache import patch_cache_control

from next.caches import ROUTES, SETTINGS, memo
from next.utils import middleware_installed

from .regex import translate
from .urlconf import MountedPatterns


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from django.http import HttpRequest
    from django.urls.resolvers import RegexPattern

    from .backends import PageBackend


SCOPE_URL_NAME: Final = "routes"
"""The URL name of the endpoint serving the route table, under the `next` namespace."""

PAGE_ROW: Final = "+"
"""The mark opening a row of the table the router owns, a page or a backend's guess."""

OTHER_ROW: Final = "-"
"""The mark opening a row of a route that is no page, which loads as a document."""

LONGEST: Final = 512
"""The longest path, script prefix included, the table decides, a longer one is none."""

_ANY: Final = PathConverter.regex
_STOPS: Final[dict[str, re.Pattern[str] | None]] = {
    StringConverter.regex: re.compile("[^/]"),
    IntConverter.regex: re.compile("[0-9]"),
    SlugConverter.regex: re.compile("[-a-zA-Z0-9_]"),
    UUIDConverter.regex: re.compile("[-0-9a-f]"),
    PathConverter.regex: None,
}
_PARAMETER: Final = re.compile(r"<(?:(?P<converter>[^>:]+):)?(?P<parameter>[^>]+)>")
_SPECIAL: Final = re.compile(r"[\\^$.|?*+()[\]{}]")
_GLOB_SPECIAL: Final = re.compile(r"[\\:*?+(){}#]")
_APART: Final = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029]")
_SEGMENT: Final = "\x00"
_GROUP: Final = re.compile(_SEGMENT)
_WILD: Final = "*"
_SLASH: Final = "/"
_ENDS: Final = frozenset({"", "$", r"\Z"})
_STARTS: Final = ("^", "\\A")
_END: Final = "$"
_SPANS: Final = 2
_DIGEST: Final = 8
_COMMON: Final = "django.middleware.common.CommonMiddleware"
_IMMUTABLE: Final = 31536000
_OURS: Final = "next."


def _escape(text: str) -> str:
    """Escape the characters JavaScript and Python regexes both read as syntax."""
    return _SPECIAL.sub(r"\\\g<0>", text)


def _glob_escape(text: str) -> str:
    """Escape the characters a URL pattern reads as syntax."""
    return _GLOB_SPECIAL.sub(r"\\\g<0>", text)


def regex_head(regex: str) -> tuple[str, str]:
    """Return the literal start of a URL pattern regex and the rest after it."""
    if "|" in regex:
        return "", regex
    text = (
        regex.removeprefix("^") if regex.startswith("^") else regex.removeprefix("\\A")
    )
    head: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        escaped = char == "\\" and not text[index + 1 : index + 2].isalnum()
        if char in ".^$*+?{}[]()|" or (char == "\\" and not escaped):
            break
        step = 2 if escaped else 1
        if text[index + step : index + step + 1] in {"?", "*", "{"}:
            break
        head.append(text[index + step - 1])
        index += step
    return "".join(head), text[index:]


@dataclass(frozen=True, slots=True)
class _Part:
    """A stretch of a route, its regex and URL pattern, its literal starts, its reach.

    An inexact stretch is cut to its literal start.
    """

    source: str = ""
    glob: str = ""
    route: str = ""
    heads: tuple[str, ...] = ("",)
    growing: bool = True
    literal: bool = True
    exact: bool = True
    bounded: bool = True
    closed: bool = False
    wild: bool = False
    spans: int = 0

    def then(self, part: _Part) -> _Part:
        """Return this stretch followed by `part`."""
        heads = self.heads
        if self.growing:
            heads = tuple(head + more for head in heads for more in part.heads)
        return _Part(
            source=self.source + part.source,
            glob=self.glob + part.glob,
            route=self.route + part.route,
            heads=heads,
            growing=self.growing and part.growing,
            literal=self.literal and part.literal,
            exact=self.exact and part.exact,
            bounded=part.bounded,
            closed=part.closed,
            wild=part.wild or (self.wild and not part.glob),
            spans=self.spans + part.spans,
        )

    def cut(self) -> _Part:
        """Return the literal start alone, past which the route is not read."""
        return _loose(self.heads[0], self.route)


def _loose(head: str, route: str) -> _Part:
    """Return a stretch read no further than its literal start `head`."""
    return _Part(
        _escape(head),
        _glob_escape(head),
        route,
        (head,),
        growing=False,
        literal=False,
        exact=False,
        bounded=False,
    )


def _stopped(stop: re.Pattern[str] | None, text: str) -> bool:
    """Return whether `text` opens with a character the converter class `stop` lacks."""
    return stop is not None and bool(text) and stop.fullmatch(text[0]) is None


def _route_part(pattern: RoutePattern) -> _Part:
    """Return a `path()` route, a converter of its own carried only once translated.

    A prefix is bounded while each converter is followed by a literal its class lacks.
    """
    route = str(pattern)
    pieces: list[str] = []
    globs: list[str] = []
    head = ""
    literal = bounded = True
    stop: re.Pattern[str] | None = None
    at = spans = 0
    for match in _PARAMETER.finditer(route):
        text = route[at : match.start()]
        bounded = bounded and (literal or _stopped(stop, text))
        pieces.append(_escape(text))
        globs.append(_glob_escape(text))
        if literal:
            head += text
        literal = False
        regex = pattern.converters[match["parameter"]].regex
        if regex in _STOPS:
            stop = _STOPS[regex]
            spans += regex == _ANY
            pieces.append(regex)
            globs.append(_WILD if regex == _ANY else _SEGMENT)
        else:
            found = translate(regex)
            if found is None or found.start or found.end:
                return _loose(head, route)
            stop = None
            spans += found.spans
            pieces.append(f"(?:{found.source})")
            globs.append(_WILD)
        at = match.end()
    tail = route[at:]
    pieces.append(_escape(tail))
    globs.append(_glob_escape(tail))
    return _Part(
        "".join(pieces),
        "".join(globs),
        route,
        (head + tail if literal else head,),
        growing=literal,
        literal=literal,
        bounded=bounded and (literal or _stopped(stop, tail)),
        closed=True,
        wild=not (literal or tail) and globs[-2] == _WILD,
        spans=spans,
    )


def _regex_part(pattern: RegexPattern, *, endpoint: bool) -> _Part:
    """Return a `re_path()` route, carried once translated and read from its start.

    An endpoint whose regex ends with `$` is matched whole, the rest from the start on.
    """
    regex = str(pattern)
    anchored = regex.startswith(_STARTS) or (endpoint and regex.endswith(_END))
    head, rest = regex_head(regex) if anchored else ("", regex)
    found = translate(regex)
    if found is None or not anchored:
        return _loose(head, regex)
    literal = rest in _ENDS
    return _Part(
        found.source,
        _glob_escape(head) + ("" if literal else _WILD),
        regex,
        (head,),
        growing=literal,
        literal=literal,
        bounded=literal and not found.end,
        closed=found.end,
        wild=not literal,
        spans=found.spans,
    )


def _locale_part(pattern: LocalePrefixPattern) -> _Part:
    """Return the language prefix as each prefix the active language may give it.

    The language a path names is the one Django reads it by, so a lookahead fixes it.
    """
    codes = {settings.LANGUAGE_CODE, *(code for code, _name in settings.LANGUAGES)}
    prefixes = sorted(
        ""
        if code == settings.LANGUAGE_CODE and not pattern.prefix_default_language
        else f"{code}/"
        for code in codes
    )
    named = [prefix for prefix in prefixes if prefix]
    choices = "|".join(map(_escape, named))
    languages = "(" + "|".join(_escape(prefix[:-1]) for prefix in named) + ")"
    if not named:
        source = glob = ""
    elif "" in prefixes:
        source, glob = f"(?=({choices}|))\\1", f"{{{languages}/}}?"
    else:
        source, glob = f"(?:{choices})", f"{languages}/"
    return _Part(source, glob, str(pattern), tuple(prefixes), literal=False)


def _part(entry: URLPattern | URLResolver) -> _Part:
    """Return the stretch one entry adds to the route above it."""
    pattern = entry.pattern
    if isinstance(pattern, LocalePrefixPattern):
        return _locale_part(pattern)
    if isinstance(pattern, RoutePattern):
        return _route_part(pattern)
    return _regex_part(pattern, endpoint=isinstance(entry, URLPattern))


@dataclass(frozen=True, slots=True)
class ScopeRow:
    """One row of the table, its regex past the script prefix and whether it is a page.

    `exact` holds while the regex matches what Django matches.
    """

    source: str
    glob: str
    route: str
    heads: tuple[str, ...]
    literal: bool
    whole: bool
    exact: bool
    page: bool
    regex: re.Pattern[str] = field(compare=False, repr=False)
    framework: bool = False
    lost: tuple[str, ...] = ()

    def matches(self, path: str) -> bool:
        """Return whether the row matches `path`, read past the script prefix."""
        return self.regex.match(path) is not None

    def encoded(self) -> str:
        """Return the row as the client reads it, its mark and then its regex."""
        return (PAGE_ROW if self.page else OTHER_ROW) + self.source


def _row(
    part: _Part, *, page: bool, lost: tuple[str, ...] = (), framework: bool = False
) -> ScopeRow:
    """Return the row of a route that ends at `part`, closed when the route is whole."""
    whole = part.exact and part.closed
    source = part.source + (_END if whole else "")
    names = itertools.count()
    glob = _GROUP.sub(lambda _found: f"{{:p{next(names)}}}", part.glob)
    return ScopeRow(
        source=source,
        glob=glob if whole or part.wild else glob + _WILD,
        route=part.route,
        heads=part.heads,
        literal=part.literal,
        whole=whole,
        exact=part.exact,
        page=page,
        regex=re.compile(source),
        framework=framework,
        lost=lost,
    )


def _serves(entry: URLPattern, backend: PageBackend | None) -> bool:
    """Return whether the router owns `entry`, a guess of its backend counted as yes."""
    if backend is not None:
        return backend.serves_page(entry) is not False
    return getattr(entry.callback, "next_page_path", None) is not None


def _owner(
    source: object, entry: URLPattern | URLResolver, backend: PageBackend | None
) -> PageBackend | None:
    """Return the backend that built `entry`, else the one around it."""
    if isinstance(source, MountedPatterns):
        return source.owner(entry) or backend
    return backend


def _held(
    entries: Iterable[URLPattern | URLResolver],
    route: str,
    source: object,
    backend: PageBackend | None,
) -> Iterator[str]:
    """Yield the route of each page under an include the table cannot read into."""
    for entry in entries:
        owner = _owner(source, entry, backend)
        here = route + str(entry.pattern)
        if isinstance(entry, URLPattern):
            if _serves(entry, owner):
                yield here
        else:
            yield from _held(entry.url_patterns, here, entry.urlconf_name, owner)


def _walk(
    entries: Iterable[URLPattern | URLResolver],
    above: _Part,
    source: object,
    backend: PageBackend | None,
) -> Iterator[ScopeRow]:
    """Yield the rows of `entries` in resolution order, each route one row.

    A route the table cannot carry, or one that backtracks past a square, is cut to its
    literal start as a row that is no page, an include standing for all it holds.
    """
    for entry in entries:
        part = _part(entry)
        if above.spans + part.spans > _SPANS:
            part = part.cut()
        owner = _owner(source, entry, backend)
        if isinstance(entry, URLPattern):
            page = _serves(entry, owner)
            module = getattr(entry.callback, "__module__", None) or ""
            framework = module.startswith(_OURS)
            if part.exact:
                yield _row(above.then(part), page=page, framework=framework)
            else:
                lost: tuple[str, ...] = (above.route + part.route,) if page else ()
                yield _row(above.then(part.cut()), page=False, lost=lost)
        elif part.exact and part.bounded:
            yield from _walk(
                entry.url_patterns, above.then(part), entry.urlconf_name, owner
            )
        else:
            route = above.route + part.route
            lost = tuple(_held(entry.url_patterns, route, entry.urlconf_name, owner))
            yield _row(above.then(part.cut()), page=False, lost=lost)


def _related(one: ScopeRow, two: ScopeRow) -> bool:
    """Return whether a literal start of one row is a prefix of a start of the other."""
    return any(
        first.startswith(second) or second.startswith(first)
        for first in one.heads
        for second in two.heads
    )


def _takes(other: ScopeRow, page: ScopeRow, *, later: bool, slash: bool) -> bool:
    """Return whether `page` may answer a path `other` matches, `True` where unknown.

    A later page may take the path, and with `slash` any page the path plus its slash.
    """
    if not _related(other, page):
        return False
    bare = slash and not (other.whole and other.source.endswith(_SLASH + _END))
    if not (later or bare):
        return False
    if other.literal and other.whole:
        head = other.heads[0]
        return (later and page.matches(head)) or (bare and page.matches(head + _SLASH))
    if page.literal and page.whole:
        head = page.heads[0]
        return (later and other.matches(head)) or (
            bare and head.endswith(_SLASH) and other.matches(head[:-1])
        )
    return True


def _pruned(rows: Sequence[ScopeRow], *, slash: bool) -> list[ScopeRow]:
    """Keep the pages and each other row a page could take a path from."""
    pages = [(index, row) for index, row in enumerate(rows) if row.page]
    return [
        row
        for index, row in enumerate(rows)
        if row.page
        or any(_takes(row, page, later=at > index, slash=slash) for at, page in pages)
    ]


def _shares(other: ScopeRow, page: ScopeRow) -> bool:
    """Return whether a link the URL pattern of `page` matches may reach `other`."""
    if page.literal and page.whole:
        return other.matches(page.heads[0])
    return _related(other, page)


def _others(rows: Sequence[ScopeRow], *, slash: bool) -> tuple[str, ...]:
    """Return the URL pattern of each route that is no page a page pattern may reach.

    A page pattern is wider than its regex, so the routes come from every row by their
    literal starts, and a slash the server appends is matched as well.
    """
    pages = [row for row in rows if row.page]
    found = [
        row.glob
        for row in rows
        if not row.page and any(_shares(row, page) for page in pages)
    ]
    if slash and any(
        not (page.glob.endswith(_SLASH) or (page.literal and page.whole))
        for page in pages
    ):
        found += [glob[:-1] for glob in found if glob.endswith(_SLASH)]
    return tuple(dict.fromkeys(found))


@dataclass(frozen=True, slots=True)
class RouteScope:
    """The route table of one URLconf under one script prefix, encoded once.

    The digest names the body, so its URL is cached for good and moves with the routes.
    """

    rows: tuple[ScopeRow, ...]
    others: tuple[str, ...]
    base: str
    slash: bool
    body: bytes
    digest: str

    def owns(self, path: str) -> bool:
        """Return whether the client router owns `path`, read past the script prefix.

        A path no row matches is read again with a slash where `APPEND_SLASH` adds one.
        """
        if len(self.base) + len(path) > LONGEST or _APART.search(path):
            return False
        slashed = self.slash and not path.endswith(_SLASH)
        for candidate in (path, path + _SLASH) if slashed else (path,):
            for row in self.rows:
                if row.matches(candidate):
                    return row.page
        return False

    def patterns(self, *, page: bool) -> list[str]:
        """Return the URL pattern of each page, or of each route a page one reaches."""
        base = _glob_escape(self.base)
        globs = [row.glob for row in self.rows if row.page] if page else self.others
        return [base + glob for glob in globs]


@dataclass(frozen=True, slots=True)
class Stranded:
    """A page that loads as a document, and the route that may take its paths if any."""

    route: str
    by: str | None = None


def _slashed() -> bool:
    """Return whether `CommonMiddleware` redirects a path that lacks its slash."""
    return bool(settings.APPEND_SLASH) and middleware_installed(_COMMON)


def _rows(urlconf: str | None) -> list[ScopeRow]:
    """Return every row of the URLconf `urlconf` names, in resolution order."""
    return list(_walk(get_resolver(urlconf).url_patterns, _Part(), None, None))


@memo(SETTINGS, ROUTES, maxsize=64)
def _scope(prefix: str, urlconf: str | None) -> RouteScope:
    """Build the table of the URLconf `urlconf` names under the script prefix."""
    every = _rows(urlconf)
    slash = _slashed()
    rows = _pruned(every, slash=slash)
    table = {"base": prefix, "slash": slash, "routes": [row.encoded() for row in rows]}
    body = json.dumps(table, separators=(",", ":")).encode()
    digest = hashlib.sha256(body).hexdigest()[:_DIGEST]
    return RouteScope(
        rows=tuple(rows),
        others=_others(every, slash=slash),
        base=prefix,
        slash=slash,
        body=body,
        digest=digest,
    )


def route_rows() -> list[ScopeRow]:
    """Return every row of the URLconf of this thread, none of them pruned."""
    return _rows(get_urlconf())


def route_scope() -> RouteScope:
    """Return the table of the URLconf and the script prefix of this thread."""
    return _scope(get_script_prefix(), get_urlconf())


def stranded_pages() -> list[Stranded]:
    """Return each page of the URLconf of this thread the router loads as a document.

    The table cannot carry the route of the page, or a row before it is wider than the
    route it stands for and may take a path of the page.
    """
    rows = route_rows()
    slash = _slashed()
    found = [Stranded(route) for row in rows for route in row.lost]
    for index, page in enumerate(rows):
        if not page.page:
            continue
        by = next(
            (
                other.route
                for other in rows[:index]
                if not other.exact and _takes(other, page, later=True, slash=slash)
            ),
            None,
        )
        if by is not None:
            found.append(Stranded(page.route, by))
    return found


def scope_url() -> str | None:
    """Return the URL of the table, `None` while the URLconf does not include it.

    Under a root `next.urls` there is no namespace, so `scope_view` is addressed itself.
    """
    digest = route_scope().digest
    for name in (f"next:{SCOPE_URL_NAME}", scope_view):
        try:
            return reverse(name, args=(digest,))
        except NoReverseMatch:
            continue
    return None


def scope_view(_request: HttpRequest, digest: str) -> HttpResponse:
    """Serve the table, cached for good under its own digest and revalidated otherwise.

    A document an older process rendered asks for its digest and gets the current table.
    """
    found = route_scope()
    response = HttpResponse(found.body, content_type="application/json")
    if digest == found.digest:
        patch_cache_control(response, public=True, max_age=_IMMUTABLE, immutable=True)
    else:
        patch_cache_control(response, no_cache=True)
    return response


__all__ = [
    "LONGEST",
    "OTHER_ROW",
    "PAGE_ROW",
    "SCOPE_URL_NAME",
    "RouteScope",
    "ScopeRow",
    "Stranded",
    "regex_head",
    "route_rows",
    "route_scope",
    "scope_url",
    "scope_view",
    "stranded_pages",
]
