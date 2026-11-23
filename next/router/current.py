"""Server pass that marks the navigation links pointing at the current URL."""

import functools
import re
from collections import deque
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from html import unescape
from typing import Final, cast
from urllib.parse import parse_qsl

from django.core.exceptions import DisallowedHost
from django.http import HttpRequest

from next.backends import resolve_backend_class
from next.caches import BoundedCache, IdentityMemo
from next.conf import next_framework_settings
from next.conf.merge import FROZEN_DEFAULTS
from next.conf.router import CONTAINER_SELECTOR, CURRENT, SCOPE, router_settings
from next.paths import ANCESTOR_MARK, PAGE_MARK, home_paths, normal_path, path_mark
from next.utils import RAW_TEXT_ELEMENTS, VOID_ELEMENTS

from .digest import fold


_DEFAULTS: Final = FROZEN_DEFAULTS[SCOPE][CURRENT]
_SHIPPED: Final[str] = _DEFAULTS["BACKEND"]

CONTAINERS: Final[tuple[str, ...]] = tuple(_DEFAULTS["CONTAINERS"])
"""The selectors of the navigation containers whose links are marked by default."""

_SPACE: Final = r"\t\n\f\r "
_EQUALS: Final = rf"[{_SPACE}]*+=[{_SPACE}]*+"
_VALUE: Final = rf"""(?:"[^"]*+"?|'[^']*+'?|[^{_SPACE}>]*+)"""
_ATTR: Final = re.compile(rf"([^{_SPACE}/>][^{_SPACE}/>=]*+)(?:{_EQUALS}({_VALUE}))?")


def _attrs_run(guard: str) -> str:
    """Return the pattern of an attribute run, each name first passing `guard`."""
    name = rf"{guard}[^{_SPACE}/>][^{_SPACE}/>=]*+"
    return rf"(?:[{_SPACE}/]++|{name}(?:{_EQUALS}{_VALUE})?)*+"


_TAG: Final = re.compile(rf"([a-z][^{_SPACE}/>]*+){_attrs_run('')}>")
_SCHEME: Final = re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*+:")

_RAW: Final[Mapping[str, str]] = {
    name: f"</{name}" for name in (*RAW_TEXT_ELEMENTS, "template")
}
"""The end tag the walk skips to past each raw-text element and an inert template."""
_TAG_ENDS: Final = "\t\n\f\r />"
_SPACE_CHARS: Final = "\t\n\f\r "
_URL_TRIM: Final = "".join(chr(code) for code in range(33))
_INSIDE_MEMO: Final = 64
_LINK_MEMO: Final = 2048
_LINK_LIMIT: Final = 512
_REGION_MEMO: Final = 64
_REGION_VARIANTS: Final = 4
_REGION_LIMIT: Final = 16384
_MARK_NAMES: Final = frozenset({"aria-current", "data-next-current"})
_NOTHING: Final = (0, 0)
_HELD: Final[Mapping[str, list[tuple[str, str]]]] = {
    PAGE_MARK: [("aria-current", "page"), ("data-next-current", "page")],
    ANCESTOR_MARK: [("data-next-current", "ancestor")],
    "": [],
}

type _Field = tuple[str, str, int, int]


class _Tag:
    """One start tag, its attributes parsed only when a check needs them."""

    __slots__ = ("_attrs", "html", "low", "name", "start", "stop")

    def __init__(self, html: str, low: str, match: re.Match[str]) -> None:
        """Hold the tag a `_TAG` match spans."""
        self.html = html
        self.low = low
        self.name = match.group(1)
        self.start = match.end(1)
        self.stop = match.end() - 1
        self._attrs: dict[str, str] | None = None

    def has(self, needle: str) -> bool:
        """Whether `needle` occurs anywhere inside the tag."""
        return self.low.find(needle, self.start, self.stop) >= 0

    def attrs(self) -> dict[str, str]:
        """Return the value of each attribute name, the first of duplicates kept."""
        if self._attrs is None:
            attrs: dict[str, str] = {}
            found = _fields(self.html, self.low, self.start, self.stop)
            for name, value, _, _ in found:
                attrs.setdefault(name, value)
            self._attrs = attrs
        return self._attrs


@dataclass(frozen=True, slots=True)
class _Selector:
    """One container selector, a tag name or an attribute with an optional value."""

    tag: str | None
    attr: str | None
    value: str | None
    needle: str

    def hit(self, tag: _Tag) -> bool:
        """Whether the start tag `tag` opens a container this selector names."""
        if self.tag is not None:
            return tag.name == self.tag
        if not tag.has(self.needle):
            return False
        value = tag.attrs().get(self.needle)
        return value is not None and (self.value is None or value == self.value)


@dataclass(frozen=True, slots=True)
class _Frame:
    """One open container with the element depth that closes it and its modes."""

    name: str
    level: int
    off: bool
    exact: bool
    query: bool


def _selector(text: str) -> _Selector | None:
    """Parse one container selector, `None` for a shape outside the three supported."""
    found = CONTAINER_SELECTOR.fullmatch(text)
    if found is None:
        return None
    if found["tag"] is not None:
        tag = found["tag"].lower()
        return _Selector(tag, None, None, f"<{tag}")
    attr = found["attr"].lower()
    values = (v for v in found.group("double", "single", "bare") if v is not None)
    return _Selector(None, attr, next(values, None), attr)


@dataclass(frozen=True, slots=True)
class _Plan:
    """The usable selectors of one container list and the pattern skipping the rest."""

    selectors: tuple[_Selector, ...]
    skip: re.Pattern[str]
    needles: tuple[str, ...]

    def last(self, low: str, hidden: tuple[int, int]) -> int:
        """Return where the last opener of `low` outside `hidden` starts, -1 for none.

        Each needle is searched only past the last opener an earlier needle found.
        """
        start, stop = hidden
        last = -1
        for needle in self.needles:
            at = low.rfind(needle, max(stop, last + 1))
            if at < 0 and last < start:
                at = low.rfind(needle, last + 1, start)
            if at >= 0:
                last = at
        return last


def _skip(
    selectors: tuple[_Selector, ...], tracked: tuple[str, ...] | None = None
) -> re.Pattern[str]:
    """Compile the pattern that skips markup the walk has nothing to do with.

    Outside a container that is all but an opener and raw text. Inside one, `tracked`
    names the open container elements, and a plain anchor is captured in group one.
    """
    names = {*_RAW, "plaintext", *(s.tag for s in selectors if s.tag)}
    attrs = sorted({s.attr for s in selectors if s.attr})
    guard = f"(?!(?:{'|'.join(map(re.escape, attrs))})[{_SPACE}/>=])" if attrs else ""
    run = _attrs_run(guard)
    ends = anchor = ""
    if tracked is not None:
        ends = f"(?!(?:{'|'.join(map(re.escape, tracked))})[{_SPACE}/>])"
        anchor = (
            "" if "a" in names or "a" in tracked else f"(<a(?=[{_SPACE}/>]){run}>)?"
        )
        names.update(tracked, "a")
    held = "|".join(map(re.escape, sorted(names)))
    return re.compile(
        rf"(?:[^<]++|<(?![a-z/!?])|<(?!(?:{held})[{_SPACE}/>])"
        rf"""[a-z][^{_SPACE}/>]*+{run}>|</{ends}[a-z][^>"']*+>)*+{anchor}"""
    )


_inside: Final = functools.lru_cache(maxsize=_INSIDE_MEMO)(_skip)


@functools.lru_cache(maxsize=16)
def _plan(containers: tuple[str, ...]) -> _Plan:
    """Return the plan of `containers`, each malformed selector dropped."""
    parsed = (_selector(text) for text in containers)
    selectors = tuple(selector for selector in parsed if selector is not None)
    needles = tuple(dict.fromkeys(selector.needle for selector in selectors))
    return _Plan(selectors, _skip(selectors), needles)


def _after(low: str, needle: str, start: int) -> int:
    """Return the index past the next `needle` from `start`, -1 when there is none."""
    at = low.find(needle, start)
    return -1 if at < 0 else at + len(needle)


def _value(raw: str) -> str:
    """Return an attribute value with its quotes dropped and its references decoded."""
    if raw[:1] in {'"', "'"}:
        raw = raw[1:-1]
    return unescape(raw) if "&" in raw else raw


def _fields(html: str, low: str, start: int, stop: int) -> list[_Field]:
    """Return the name, decoded value and span of each attribute in a tag."""
    found: list[_Field] = []
    for match in _ATTR.finditer(low, start, stop):
        begin, end = match.span(2)
        value = _value(html[begin:end]) if begin >= 0 else ""
        found.append((match.group(1), value, match.start(), match.end()))
    return found


def _lead(low: str, start: int) -> int:
    """Return where the run of whitespace ending at `start` begins."""
    while low[start - 1] in _SPACE_CHARS:
        start -= 1
    return start


def _reference(href: str) -> tuple[str, str] | None:
    """Return the location and the query of a link target, `None` for a fragment."""
    ref = href.strip(_URL_TRIM).replace("\t", "").replace("\n", "")
    ref = ref.replace("\r", "").replace("\\", "/")
    if not ref or ref.startswith("#"):
        return None
    location, _, query = ref.partition("#")[0].partition("?")
    return location, query


@dataclass(frozen=True, slots=True)
class _Link:
    """What one anchor start tag says, every index counted from its `<`.

    A fact of the tag text alone, so one site-wide link is parsed once for all requests.
    """

    off: bool
    match: str | None
    ref: tuple[str, str] | None
    path: str | None
    settled: str | None
    drops: tuple[tuple[int, int, str], ...]
    at: int


def _read_link(raw: str) -> _Link:
    """Parse the anchor start tag `raw`, its rooted target normalised."""
    low = fold(raw)
    stop = len(raw) - 1
    attrs: dict[str, str] = {}
    held: list[tuple[str, str]] = []
    drops: list[tuple[int, int, str]] = []
    for name, value, start, end in _fields(raw, low, 2, stop):
        attrs.setdefault(name, value)
        if name in _MARK_NAMES:
            held.append((name, value))
            joins = low[end] not in _SPACE_CHARS and low[end] != ">"
            drops.append((start if joins else _lead(low, start), end, ""))
    held.sort()
    href = attrs.get("href")
    ref = None if href is None else _reference(href)
    rooted = ref is not None and ref[0].startswith("/") and not ref[0].startswith("//")
    return _Link(
        off=attrs.get("data-next-current") == "off",
        match=attrs.get("data-next-match"),
        ref=ref,
        path=normal_path(ref[0]) if ref is not None and rooted else None,
        settled=next((mark for mark, said in _HELD.items() if said == held), None),
        drops=tuple(drops),
        at=_lead(low, stop),
    )


_known_link: Final = functools.lru_cache(maxsize=_LINK_MEMO)(_read_link)


def _link(raw: str) -> _Link:
    """Return the parsed anchor `raw`, remembered unless it is too long to keep."""
    return _read_link(raw) if len(raw) > _LINK_LIMIT else _known_link(raw)


def _modes(value: str | None, *, exact: bool, query: bool) -> tuple[bool, bool]:
    """Return the exact and query modes `data-next-match` asks for, else the given."""
    if value is None:
        return exact, query
    tokens = value.lower().split()
    return "exact" in tokens, "query" in tokens


def _grouped(query: str) -> dict[str, list[str]]:
    """Return the sorted values of each parameter a query string names."""
    grouped: dict[str, list[str]] = {}
    for name, value in parse_qsl(query, keep_blank_values=True):
        grouped.setdefault(name, []).append(value)
    for values in grouped.values():
        values.sort()
    return grouped


class _Current:
    """The current URL every link of one pass is compared against."""

    __slots__ = ("_homes", "_params", "base", "origin", "path", "query", "raw", "read")

    def __init__(
        self,
        path: str,
        query: str,
        origin: str,
        homes: Callable[[], Collection[str]] | None,
    ) -> None:
        """Normalise the current path once for the whole pass."""
        self.raw = path if path.startswith("/") else f"/{path}"
        self.base = self.raw[: self.raw.rfind("/") + 1]
        self.path = normal_path(self.raw)
        self.query = query
        self.origin = origin.rstrip("/").lower()
        self.read = homes
        self._params: dict[str, list[str]] | None = None
        self._homes: Collection[str] | None = None

    def homes(self) -> Collection[str]:
        """Return the home paths of the pass, read on first use."""
        if self._homes is None:
            self._homes = () if self.read is None else self.read()
        return self._homes

    def params(self) -> dict[str, list[str]]:
        """Return the parameters of the current query, parsed on first use."""
        if self._params is None:
            self._params = _grouped(self.query)
        return self._params

    def same_params(self, query: str) -> bool:
        """Whether every parameter `query` names holds the same values here."""
        have = self.params()
        return all(have.get(name) == values for name, values in _grouped(query).items())

    def local(self, authority: str, scheme: str) -> str | None:
        """Return the path of an absolute reference when it names this origin."""
        host, slash, rest = authority.partition("/")
        if not self.origin or f"{scheme}://{host}".lower() != self.origin:
            return None
        return slash + rest or "/"

    def resolve(self, location: str) -> str | None:
        """Return the normal path of an unrooted location when it names this origin."""
        resolved: str | None
        if not location:
            resolved = self.raw
        elif location.startswith("//"):
            resolved = self.local(location[2:], self.origin.partition(":")[0])
        elif (scheme := _SCHEME.match(location)) is not None:
            rest = location[scheme.end() :]
            authority = rest[2:] if rest.startswith("//") else ""
            resolved = self.local(authority, location[: scheme.end() - 1])
        else:
            resolved = self.base + location
        return None if resolved is None else normal_path(resolved)

    def mark(self, link: _Link, *, exact: bool, query: bool) -> str:
        """Return the attributes `link` gains, empty for an unmarked one."""
        if link.ref is None:
            return ""
        location = link.path
        if location is None:
            location = self.resolve(link.ref[0])
            if location is None:
                return ""
        if (query or not link.ref[0]) and not self.same_params(link.ref[1]):
            return ""
        return path_mark(location, self.path, self.homes, exact=exact)


type _Note = tuple[int, _Link, bool, bool]
type _Region = tuple[str, tuple[_Note, ...]]


_walked: BoundedCache[tuple[tuple[_Selector, ...], str], deque[_Region]] = BoundedCache(
    _REGION_MEMO
)


def _regions(selectors: tuple[_Selector, ...], opener: str) -> deque[_Region]:
    """Return the walked outermost containers `opener` began, the newest first.

    A container is a fact of its own text, so a sidebar every page repeats is walked
    once and each later pass only compares its links against the current URL.
    """
    key = (selectors, opener)
    held = _walked.get(key)
    if held is None:
        held = _walked[key] = deque(maxlen=_REGION_VARIANTS)
    return held


class _Scan:
    """One linear walk over a document that collects the marks to insert."""

    __slots__ = (
        "current",
        "cuts",
        "html",
        "inside",
        "known",
        "last",
        "low",
        "notes",
        "origin",
        "selectors",
        "skip",
        "stack",
        "tracked",
    )

    def __init__(
        self, html: str, low: str, plan: _Plan, current: _Current, last: int
    ) -> None:
        """Start before the first tag, `last` the start of the last container opener."""
        self.html = html
        self.low = low
        self.selectors = plan.selectors
        self.skip = plan.skip
        self.inside = plan.skip
        self.current = current
        self.last = last
        self.stack: list[_Frame] = []
        self.tracked: dict[str, int] = {}
        self.cuts: list[tuple[int, int, str]] = []
        self.origin = -1
        self.notes: list[_Note] = []
        self.known: deque[_Region] = deque()

    def run(self) -> str:
        """Walk the document and return it with the collected marks inserted."""
        low = self.low
        skip = self.skip
        pos = 0
        while pos >= 0:
            if self.stack:
                found = cast("re.Match[str]", self.inside.match(low, pos))
                pos = found.end()
                if found.lastindex:
                    self.anchor(found.start(1), pos)
                    continue
            elif pos > self.last:
                break
            else:
                pos = cast("re.Match[str]", skip.match(low, pos)).end()
                if pos > self.last:
                    break
            lt = low.find("<", pos)
            if lt < 0:
                break
            pos = self.step(lt)
        return self.splice()

    def step(self, lt: int) -> int:
        """Consume the markup at `lt` and return where the walk resumes, -1 to stop."""
        low = self.low
        nxt = low[lt + 1 : lt + 2]
        if "a" <= nxt <= "z":
            match = _TAG.match(low, lt + 1)
            return -1 if match is None else self.start_tag(match)
        if nxt == "/":
            return self.end_tag(lt)
        if nxt == "!":
            return self.declaration(lt)
        return _after(low, ">", lt + 2)

    def start_tag(self, match: re.Match[str]) -> int:
        """Handle one start tag, skipping raw text and opening containers."""
        tag = _Tag(self.html, self.low, match)
        close = _RAW.get(tag.name)
        if close is not None:
            return self.raw_end(close, match.end())
        if tag.name == "plaintext":
            return -1
        if tag.name == "a" and self.stack:
            self.anchor(match.start() - 1, match.end())
        if tag.name not in VOID_ELEMENTS:
            return self.open(tag, match.start() - 1, match.end())
        return match.end()

    def raw_end(self, close: str, start: int) -> int:
        """Return the start of the end tag that closes a raw text element."""
        low = self.low
        at = low.find(close, start)
        while at >= 0:
            after = low[at + len(close) : at + len(close) + 1]
            if not after or after in _TAG_ENDS:
                return at
            at = low.find(close, at + len(close))
        return -1

    def end_tag(self, lt: int) -> int:
        """Handle one end tag, closing the container it ends."""
        low = self.low
        nxt = low[lt + 2 : lt + 3]
        if "a" <= nxt <= "z":
            match = _TAG.match(low, lt + 2)
            if match is None:
                return -1
            self.close(match.group(1))
            if self.origin >= 0 and not self.stack:
                self.keep(match.end())
            return match.end()
        if nxt == ">":
            return lt + 3
        return _after(low, ">", lt + 2)

    def declaration(self, lt: int) -> int:
        """Skip a comment, a CDATA section or a doctype opening at `lt`."""
        low = self.low
        if low.startswith("<!--", lt):
            close = low.find("-->", lt + 2)
            bang = low.find("--!>", lt + 4, close if close >= 0 else len(low))
            if bang >= 0:
                return bang + 4
            return -1 if close < 0 else close + 3
        if self.html.startswith("<![CDATA[", lt):
            return _after(low, "]]>", lt + 9)
        return _after(low, ">", lt + 2)

    def open(self, tag: _Tag, start: int, end: int) -> int:
        """Count one open element, push a container and return where the walk resumes.

        An outermost container walked before is replayed and skipped whole.
        """
        tracked = self.tracked
        container = any(selector.hit(tag) for selector in self.selectors)
        if not container and tag.name not in tracked:
            return end
        if not self.stack and end - start <= _LINK_LIMIT:
            html = self.html
            self.known = _regions(self.selectors, html[start:end])
            for text, notes in tuple(self.known):
                if html.startswith(text, start):
                    for at, link, exact, query in notes:
                        self.rewrite(start + at, link, exact=exact, query=query)
                    return start + len(text)
            self.origin = start
        level = tracked.get(tag.name, 0) + 1
        tracked[tag.name] = level
        if container:
            self.stack.append(self.frame(tag, level))
            self.inside = _inside(self.selectors, tuple(sorted(tracked)))
        return end

    def keep(self, end: int) -> None:
        """Remember the outermost container that closed at `end` with its links."""
        start = self.origin
        if end - start <= _REGION_LIMIT:
            self.known.appendleft((self.html[start:end], tuple(self.notes)))
        self.origin = -1
        self.notes.clear()

    def frame(self, tag: _Tag, level: int) -> _Frame:
        """Return the frame of a container, its modes inherited where unset."""
        parent = self.stack[-1] if self.stack else None
        attrs = tag.attrs() if tag.has("data-next-") else {}
        off = attrs.get("data-next-current") == "off" or (
            parent is not None and parent.off
        )
        exact, query = _modes(
            attrs.get("data-next-match"),
            exact=parent is not None and parent.exact,
            query=parent is not None and parent.query,
        )
        return _Frame(tag.name, level, off, exact, query)

    def close(self, name: str) -> None:
        """Count one closed element and pop the container it ends with its inner."""
        tracked = self.tracked
        level = tracked.get(name, 0)
        if level == 0:
            return
        tracked[name] = level - 1
        stack = self.stack
        for index in range(len(stack) - 1, -1, -1):
            if stack[index].name == name:
                if stack[index].level >= level:
                    del stack[index:]
                if not stack:
                    tracked.clear()
                return

    def anchor(self, start: int, end: int) -> None:
        """Read one anchor inside the innermost container and rewrite its marks."""
        frame = self.stack[-1]
        link = _link(self.html[start:end])
        if frame.off or link.off:
            return
        exact, query = _modes(link.match, exact=frame.exact, query=frame.query)
        if self.origin >= 0:
            self.notes.append((start - self.origin, link, exact, query))
        self.rewrite(start, link, exact=exact, query=query)

    def rewrite(self, start: int, link: _Link, *, exact: bool, query: bool) -> None:
        """Queue the rewrite of the marks of the anchor that opens at `start`.

        A removed mark takes its leading whitespace only when no neighbours would join.
        """
        mark = self.current.mark(link, exact=exact, query=query)
        if link.settled == mark:
            return
        cuts = self.cuts
        for begin, stop, text in link.drops:
            cuts.append((start + begin, start + stop, text))
        if mark:
            cuts.append((start + link.at, start + link.at, mark))

    def splice(self) -> str:
        """Return the document with every queued rewrite applied, itself when none."""
        if not self.cuts:
            return self.html
        html = self.html
        parts: list[str] = []
        last = 0
        for start, end, text in self.cuts:
            parts.append(html[last:start])
            parts.append(text)
            last = end
        parts.append(html[last:])
        return "".join(parts)


class CurrentLinks:
    """The settings-named backend that marks current links in rendered HTML."""

    def __init__(self, containers: tuple[str, ...] = CONTAINERS) -> None:
        """Hold the container selectors the pass scans."""
        self.containers = containers

    def holds(self, html: str) -> bool:
        """Whether `html` opens a container, which a template plan asks once."""
        return _plan(self.containers).last(fold(html), _NOTHING) >= 0

    def mark(
        self,
        html: str,
        path: Callable[[], str],
        origin: Callable[[], str] | None = None,
        *,
        hidden: tuple[int, int] = _NOTHING,
    ) -> str:
        """Return `html` marked against the request path with its query `path` reads.

        `hidden` spans framework payload, so its selector text opens no container.
        """
        plan = _plan(self.containers)
        low = fold(html)
        last = plan.last(low, hidden)
        if last < 0:
            return html
        location, _, query = path().partition("#")[0].partition("?")
        host = "" if origin is None else origin()
        current = _Current(location, query, host, home_paths)
        return _Scan(html, low, plan, current, last).run()


def _links() -> CurrentLinks | None:
    """Build the backend `ROUTER["CURRENT"]` names, `None` while the pass is off."""
    current = router_settings()
    if not current.current_enabled:
        return None
    dotted = current.current_backend
    klass = (
        CurrentLinks
        if dotted == _SHIPPED
        else resolve_backend_class({"BACKEND": dotted}, base=CurrentLinks)
    )
    return klass(current.current_containers)


_LINKS: Final[IdentityMemo[CurrentLinks | None]] = IdentityMemo()


def current_links() -> CurrentLinks | None:
    """Return the active-link backend the settings name, `None` while it is off.

    The backend is built again once the settings replace the merged `ROUTER` mapping.
    """
    return _LINKS.get(next_framework_settings.ROUTER, _links)


def origin_of(request: HttpRequest) -> str:
    """Return the scheme and host of `request`, empty when the host is not allowed."""
    try:
        host = request.get_host()
    except DisallowedHost:
        return ""
    return f"{request.scheme}://{host}"


__all__ = ["CONTAINERS", "CurrentLinks", "current_links", "origin_of"]
