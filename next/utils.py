"""Cross-area helpers for paths, `DIRS` entries, page trees, routes, and edit watching.

It sits below the subpackages that share it, so no shared object closes a cycle.
"""

from __future__ import annotations

import functools
import importlib.machinery
import importlib.util
import logging
import os
import re
import sys
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from itertools import groupby
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple, TypeGuard, override
from urllib.parse import unquote_to_bytes

from django.conf import settings
from django.core.exceptions import DisallowedRedirect
from django.http import HttpResponseRedirect
from django.template.base import NodeList
from django.utils.encoding import repercent_broken_unicode
from django.utils.module_loading import import_string
from django.utils.regex_helper import normalize

from next.caches import DEFAULT_CACHE_SIZE
from next.diagnostics import Capture
from next.errors import InvalidDirsError


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Generator, Iterable, Iterator, Mapping

    from django.template.base import Node


logger = logging.getLogger(__name__)


# The bound every ancestor walk shares, so none reaches the filesystem root.
MAX_ANCESTOR_WALK_DEPTH = 64

WEB_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
"""The URL schemes a page, a head tag or a sitemap may point a crawler at."""


def is_int(value: object) -> TypeGuard[int]:
    """Whether `value` is an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def int_between(value: object, low: int, high: int) -> TypeGuard[int]:
    """Whether `value` is an int that is not a bool, from `low` up to `high`."""
    return is_int(value) and low <= value <= high


def positive_number(value: object, ceiling: float) -> TypeGuard[float]:
    """Whether `value` is a finite int or float above zero and up to `ceiling`."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and 0 < value <= min(ceiling, sys.float_info.max)
    )


def non_negative_number(value: object, ceiling: float) -> TypeGuard[float]:
    """Whether `value` is a finite int or float from zero up to `ceiling`."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and 0 <= value <= min(ceiling, sys.float_info.max)
    )


def after_fork(reset: Callable[[], object]) -> None:
    """Run `reset` in every forked child, nothing on a platform that cannot fork."""
    register = getattr(os, "register_at_fork", None)
    if register is not None:
        register(after_in_child=reset)


ROUTE_BRACKET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[\[(?P<wild>[^\[\]]+)\]\]|\[(?P<param>[^\[\]]+)\]"
)
"""The bracket segments of a trail, a double-bracket wildcard or a typed parameter."""

ROUTE_GROUP_PATTERN: Final[re.Pattern[str]] = re.compile(r"\(([^\W\d][\w-]*)\)")
"""A route group directory name, an identifier in parentheses the URL leaves out."""

INTERCEPT_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?P<marker>\(\.\)|(?:\(\.\.\))+|\(\.\.\.\))(?P<rest>[^()\s/]+)"
)
"""An intercept directory name, one marker kind then the segment it targets."""


_DJANGO_PARAMETER: Final[re.Pattern[str]] = re.compile(r"<([^<>]+)>")

_REGEX_PARAMETER: Final[re.Pattern[str]] = re.compile(r"%\((\w+)\)s")

_UNREVERSIBLE_TRAIL: Final = "[[route]]"
"""The trail of a `re_path()` route Django cannot reverse, a wildcard listing none."""


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def prefix_trail(prefix: str) -> str:
    """Return a Django `path()` prefix as a trail, `<int:pk>` read as `[int:pk]`."""
    return _DJANGO_PARAMETER.sub(r"[\1]", prefix.strip("/"))


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def regex_trail(regex: str) -> str:
    """Return a `re_path()` route as a trail, read the way Django reverses it.

    A route Django cannot reverse is a wildcard, so no page under it reads as static.
    """
    try:
        route = normalize(regex)[0][0]
    except ValueError:
        return _UNREVERSIBLE_TRAIL
    if not route and regex.strip("^$/"):
        return _UNREVERSIBLE_TRAIL
    return _REGEX_PARAMETER.sub(r"[\1]", route.strip("/"))


def join_trail(head: str, tail: str) -> str:
    """Join two trails with one slash, either of them possibly empty."""
    if not head:
        return tail
    return f"{head}/{tail}" if tail else head


def is_dynamic_trail(url_path: str) -> bool:
    """Whether `url_path` carries a bracket segment, so it routes with parameters."""
    return ROUTE_BRACKET_PATTERN.search(url_path) is not None


def is_route_group(name: str) -> bool:
    """Whether the directory `name` is a route group, which adds nothing to the URL."""
    return name[:1] == "(" and ROUTE_GROUP_PATTERN.fullmatch(name) is not None


def strip_route_groups(raw_trail: str) -> str:
    """Return a file-system trail with its route group segments left out."""
    if "(" not in raw_trail:
        return raw_trail
    return "/".join(
        segment for segment in raw_trail.split("/") if not is_route_group(segment)
    )


def is_intercept_name(name: str) -> bool:
    """Whether the router reads the directory `name` as an intercept, valid or not."""
    return name[:2] == "(."


def parse_intercept(name: str) -> tuple[str, str] | None:
    """Return the marker and the targeted segment of an intercept directory `name`."""
    if not is_intercept_name(name):
        return None
    match = INTERCEPT_PATTERN.fullmatch(name)
    return None if match is None else (match["marker"], match["rest"])


def resolve_intercept(scope_trail: str, marker: str, rest: str) -> str | None:
    """Return the URL trail an intercept targets, `None` when `(..)` passes the root.

    `scope_trail` is the group-free URL trail of the directory holding the intercept.
    """
    if marker == "(.)":
        return join_trail(scope_trail, rest)
    if marker == "(...)":
        return rest
    segments = scope_trail.split("/") if scope_trail else []
    up = len(marker) // len("(..)")
    if up > len(segments):
        return None
    return "/".join([*segments[: len(segments) - up], rest])


@dataclass(frozen=True, slots=True)
class InterceptPage:
    """A page found under an intercept directory and the routed trail it serves over.

    `file` is a `page.py`, virtual beside a lone `template.djx`, as the walk yields one.
    """

    target_trail: str
    file: Path
    source: Path
    marker: str


def normalise_route_name(raw_name: str) -> str:
    """Return the route name a bracket segment maps to, reading `-` as `_`."""
    return raw_name.replace("-", "_")


def decode_url_path(path: str) -> str:
    """Return a percent-encoded URL path decoded the way Django builds `request.path`.

    Decoded only once split from its query, so an encoded `?` stays in its segment.
    """
    return repercent_broken_unicode(unquote_to_bytes(path)).decode()


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def resolved_tree(path: Path) -> Path:
    """Return the resolved form of a page tree, memoised across the process."""
    return path.resolve()


# Memos of resolved paths that a layer keys its own way, dropped with this one.
_RESOLUTION_CLEARERS: list[Callable[[], None]] = []


def on_forget_resolved_trees(clear: Callable[[], None]) -> None:
    """Register a memo of resolved paths to drop whenever this module drops its own."""
    _RESOLUTION_CLEARERS.append(clear)


def forget_resolved_trees() -> None:
    """Drop every memoised resolution, so a re-pointed tree is read again."""
    resolved_tree.cache_clear()
    for clear in _RESOLUTION_CLEARERS:
        clear()


def stat_mtime_ns(path: Path) -> int | None:
    """Return the nanosecond mtime of `path`, or `None` when it does not stat.

    A float timestamp would round two distinct writes into one value.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def is_within(path: Path, root: Path) -> bool:
    """Return whether the resolved `path` is `root` or lies under it, by parts.

    It is far cheaper than `Path.is_relative_to` on a hot path.
    """
    return path.parts[: len(root.parts)] == root.parts


class _SourceLoader(importlib.machinery.SourceFileLoader):
    """Validate the cached bytecode of a source on its nanosecond mtime.

    Whole-second checks serve a stale `.pyc` after a same-size rewrite in one second.
    """

    @override
    def path_stats(self, path: str) -> Mapping[str, Any]:
        """Return the mtime in nanoseconds, whose low 32 bits the `.pyc` keeps."""
        stat = Path(path).stat()
        return {"mtime": stat.st_mtime_ns, "size": stat.st_size}


def exec_module_file(path: Path, name: str) -> types.ModuleType | None:
    """Execute the file at `path` as a fresh module, raising whatever its body raises.

    `None` means importlib knows no loader for the suffix of `path`.
    """
    source = path.suffix in importlib.machinery.SOURCE_SUFFIXES
    loader = _SourceLoader(name, str(path)) if source else None
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True, slots=True)
class TreeSource[E: Exception]:
    """One Python file at the top of a page tree, with its executed module or failure.

    `stamp` is the mtime at execution, so a watched process reruns the file on an edit.
    """

    path: Path
    module: types.ModuleType | None = None
    error: E | None = None
    stamp: int | None = None

    def stale(self) -> bool:
        """Return whether the file was deleted or rewritten since it ran."""
        return stat_mtime_ns(self.path) != self.stamp


def load_tree_source[E: Exception](
    path: Path, module_name: str, error: Callable[[Path], E]
) -> TreeSource[E] | None:
    """Execute the file at `path` as `module_name`, `None` when there is no file.

    An exception it raises is logged and kept as the `__cause__` of `error(path)`.
    """
    if not path.is_file():
        return None
    stamp = stat_mtime_ns(path)
    try:
        module = exec_module_file(path, module_name)
    except Exception as exc:
        logger.exception("%s failed to import, so it declares nothing", path)
        failure = error(path)
        failure.__cause__ = exc
        return TreeSource(path, error=failure, stamp=stamp)
    return TreeSource(path, module, stamp=stamp)


@dataclass(frozen=True, slots=True)
class PageRoot:
    """A page tree a router routes, with the label a report names it by.

    A `pages()` tree carries its include route, in Django's spelling and as a trail.
    The key names the tree in co-located static names, and none serves no assets.
    """

    path: Path
    label: str
    prefix: str | None = None
    namespace: str = "next"
    trail: str | None = None
    key: str | None = None

    def route_trail(self, trail: str = "") -> str:
        """Return the trail of the route a page at the tree-relative `trail` answers."""
        head = prefix_trail(self.prefix or "") if self.trail is None else self.trail
        return join_trail(head, trail)


UNMOUNTED: Final = PageRoot(path=Path(), label="")
"""A tree with no `pages()` include, whose pages route by their own trail."""


def page_roots_shape_error(source: str, roots: list[Any]) -> str | None:
    """Return why `roots` is no list of page trees, or `None` when it is one."""
    for root in roots:
        if not isinstance(root, PageRoot):
            return (
                f"{source} reported {type(root).__name__} instead of a "
                "next.urls.PageRoot page tree"
            )
        # Widened from `Path`, as a dataclass validates no field.
        tree: object = root.path
        if not isinstance(tree, Path):
            return (
                f"{source} reported a page tree whose path is "
                f"{type(tree).__name__} instead of pathlib.Path"
            )
    return None


def resolve_base_dir() -> Path | None:
    """Return ``settings.BASE_DIR`` as a ``pathlib.Path``, or ``None``."""
    raw = getattr(settings, "BASE_DIR", None)
    if isinstance(raw, Path):
        return raw
    if isinstance(raw, str):
        return Path(raw)
    return None


def template_edits_watched() -> bool:
    """Whether the caches re-read the disk, since autoreload misses `.djx` edits."""
    return bool(settings.DEBUG)


def _dir_entry_candidate(item: Path, base_dir: Path | None) -> Path | None:
    """Return the path a ``DIRS`` entry could name, before it is resolved.

    Only ``BASE_DIR`` can resolve a relative entry, so without one it names a segment.
    """
    if item.is_absolute():
        return item
    return None if base_dir is None else base_dir / item


def _dir_entry_segment_name(item: Path) -> str:
    """Return the URL segment name a ``DIRS`` entry that names no tree carries.

    A Windows-style entry read on POSIX has no separator, so split the slashed text.
    """
    if item.is_absolute():
        return item.name
    text = str(item).replace("\\", "/")
    return Path(text).name if "/" in text else item.name


def _iter_dir_entries(entries: Iterable[Any] | None) -> Generator[Path, None, None]:
    """Yield every ``DIRS`` entry that names anything, as a path."""
    for raw in entries or ():
        if raw is None:
            continue
        item = Path(raw) if not isinstance(raw, Path) else raw
        # `Path("")` spells itself ".", so the one test covers the empty entry.
        if str(item) != ".":
            yield item


def classify_dirs_entries(
    entries: Iterable[Any] | None, base_dir: Path | None
) -> tuple[list[Path], frozenset[str]]:
    """Split ``DIRS`` into directory roots and URL segment names (file router).

    Settled here rather than per reader, since every caller hands in one settings entry.
    """
    if isinstance(entries, str | bytes):
        raise InvalidDirsError(entries)
    try:
        items = list(_iter_dir_entries(entries))
    except TypeError as exc:
        raise InvalidDirsError(entries) from exc
    path_roots: list[Path] = []
    segments: set[str] = set()
    for item in items:
        candidate = _dir_entry_candidate(item, base_dir)
        # Resolved before the probe, so `..` past a missing directory still resolves.
        resolved = None if candidate is None else resolved_tree(candidate)
        if resolved is not None and resolved.is_dir():
            path_roots.append(resolved)
        else:
            name = _dir_entry_segment_name(item)
            # Separators alone name no segment, and an empty name would be skipped.
            if name:
                segments.add(name)

    return path_roots, frozenset(segments)


PAGE_FILES: Final = frozenset({"page.py", "template.djx"})
"""The files that make a directory a page, a `template.djx` alone a virtual one."""

LAYOUT_TEMPLATE: Final = "layout.djx"
"""The template that wraps every page of its directory and the directories below."""


def walk_page_tree(
    tree_root: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    on_skipped_dir: Callable[[Path, Path, str], None] | None = None,
    on_intercept: Callable[[InterceptPage], None] | None = None,
) -> Generator[tuple[str, Path], None, None]:
    """Yield `(url_path, page_file)` for every page under `tree_root`, groups left out.

    A group joins the level holding it, and an intercept page goes to `on_intercept`.
    """
    walk = _PageTreeWalk(
        tree_root, frozenset(skip_dir_names), on_skipped_dir, on_intercept
    )
    yield from walk.visit(((tree_root, ""),), "")


_CONVERTER_RANK: Final[dict[str, int]] = {"int": 0, "uuid": 0, "slug": 1, "path": 3}
"""How much a converter matches, narrowest first, `str` and the bare form at 2.

A converter a project registers is read as `slug`, narrower than `str`.
"""


def _route_rank(name: str) -> tuple[int, int, int, str]:
    """Rank a directory among its siblings, the most specific route first.

    Static, then parameter, then catch-all, then more literal text, narrower converter.
    """
    param = wildcard = False
    width = 0
    for match in ROUTE_BRACKET_PATTERN.finditer(name):
        wild = match.group("wild")
        param = True
        wildcard = wildcard or wild is not None
        label, typed, _name = (wild or match.group("param")).partition(":")
        converter = label if typed else ("path" if wild is not None else "str")
        width += _CONVERTER_RANK.get(converter, 2 if converter == "str" else 1)
    literal = len(ROUTE_BRACKET_PATTERN.sub("", name))
    return wildcard + param, -literal, width, name


class PageDir(NamedTuple):
    """One listing of a page-tree directory, its file names and its subdirectories."""

    files: frozenset[str]
    children: tuple[Path, ...]


def list_page_dir(directory: Path) -> PageDir:
    """List `directory` once, its subdirectories in `_route_rank` order."""
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
    except OSError as e:
        logger.debug("Cannot list directory %s: %s", directory, e)
        return PageDir(frozenset(), ())
    files: set[str] = set()
    children: list[Path] = []
    for entry in entries:
        # `is_dir` reads the type the listing returned, so only a symlink costs a stat.
        if entry.is_dir():
            children.append(Path(entry.path))
        else:
            files.add(entry.name)
    children.sort(key=lambda child: _route_rank(child.name))
    return PageDir(frozenset(files), tuple(children))


def routes_into(name: str, skip_dir_names: frozenset[str]) -> bool:
    """Return whether the router descends into a directory named `name`."""
    return not name.startswith("_") and name not in skip_dir_names


class _ChildKind(Enum):
    """How the walk treats one subdirectory of a page-tree directory."""

    ROUTED = "routed"
    GROUP = "group"
    INTERCEPT = "intercept"
    SKIPPED = "skipped"


def _child_kind(name: str, skip_dir_names: frozenset[str]) -> _ChildKind:
    """Classify the subdirectory `name`, the one place the walk reads a child name."""
    if not routes_into(name, skip_dir_names):
        return _ChildKind.SKIPPED
    if name[:1] != "(":
        return _ChildKind.ROUTED
    if is_intercept_name(name):
        return _ChildKind.INTERCEPT
    if ROUTE_GROUP_PATTERN.fullmatch(name) is not None:
        return _ChildKind.GROUP
    return _ChildKind.ROUTED


@dataclass(frozen=True, slots=True)
class _PageTreeWalk:
    """One walk of a page tree and what every directory of it shares.

    A skipped folder is reported with its file-system trail, as component scope reads.
    """

    tree_root: Path
    skip_dir_names: frozenset[str]
    on_skipped_dir: Callable[[Path, Path, str], None] | None
    on_intercept: Callable[[InterceptPage], None] | None

    def visit(
        self, directories: tuple[tuple[Path, str], ...], url_trail: str
    ) -> Generator[tuple[str, Path], None, None]:
        """Yield the pages of one URL level, then descend into its routed children.

        The same-named children of its directories descend together, ranked as one.
        """
        pages: list[Path] = []
        kids: list[tuple[Path, str]] = []
        marked: list[tuple[Path, str]] = []
        merged = len(directories) > 1
        for directory, raw_trail in directories:
            merged = self.flatten(directory, raw_trail, pages, kids, marked) or merged
        for page in pages:
            yield url_trail, page
        for source, source_raw in marked:
            self.intercept(source, url_trail, source_raw)
        if merged:
            kids.sort(key=lambda kid: _route_rank(kid[0].name))
        for name, segment in groupby(kids, key=lambda kid: kid[0].name):
            yield from self.visit(tuple(segment), join_trail(url_trail, name))

    def flatten(
        self,
        directory: Path,
        raw_trail: str,
        pages: list[Path],
        kids: list[tuple[Path, str]],
        marked: list[tuple[Path, str]] | None,
    ) -> bool:
        """Collect the page, routed children and intercepts of `directory` and groups.

        It returns whether a group contributed, and `marked=None` drops an intercept.
        """
        listing = list_page_dir(directory)
        if not listing.files.isdisjoint(PAGE_FILES):
            pages.append(directory / "page.py")
        groups: list[Path] = []
        for child in listing.children:
            kind = _child_kind(child.name, self.skip_dir_names)
            if kind is _ChildKind.ROUTED:
                kids.append((child, join_trail(raw_trail, child.name)))
            elif kind is _ChildKind.GROUP:
                groups.append(child)
            elif kind is _ChildKind.INTERCEPT:
                if marked is not None:
                    marked.append((child, join_trail(raw_trail, child.name)))
            elif self.on_skipped_dir is not None:
                self.on_skipped_dir(child, self.tree_root, raw_trail)
        if not groups:
            return False
        groups.sort(key=lambda group: group.name)
        for group in groups:
            self.flatten(group, join_trail(raw_trail, group.name), pages, kids, marked)
        return True

    def intercept(self, source: Path, scope_trail: str, raw_trail: str) -> None:
        """Walk the intercept directory `source` when its name resolves to a target.

        A malformed name or a `(..)` past the root is left for the system checks.
        """
        if self.on_intercept is None and self.on_skipped_dir is None:
            return
        parsed = parse_intercept(source.name)
        if parsed is None:
            return
        marker, rest = parsed
        target_trail = resolve_intercept(scope_trail, marker, rest)
        if target_trail is not None:
            self.mark(source, source, marker, target_trail, raw_trail)

    def mark(
        self,
        directory: Path,
        source: Path,
        marker: str,
        target_trail: str,
        raw_trail: str,
    ) -> None:
        """Report the pages under one directory of an intercept, never routing them."""
        pages: list[Path] = []
        kids: list[tuple[Path, str]] = []
        self.flatten(directory, raw_trail, pages, kids, None)
        if self.on_intercept is not None:
            for page in pages:
                self.on_intercept(InterceptPage(target_trail, page, source, marker))
        for child, child_raw in kids:
            child_trail = join_trail(target_trail, child.name)
            self.mark(child, source, marker, child_trail, child_raw)


_MIDDLEWARE_CLASSES: dict[str, type] = {}


def _middleware_class(path: str) -> type | None:
    """Import a `MIDDLEWARE` entry, `None` when it does not import as a class.

    Only a class is kept, so an entry fixed after a failed import is read again.
    """
    known = _MIDDLEWARE_CLASSES.get(path)
    if known is not None:
        return known
    capture = Capture()
    with capture:
        found = import_string(path)
    if capture.failed or not isinstance(found, type):
        return None
    _MIDDLEWARE_CLASSES[path] = found
    return found


def is_middleware(entry: object, base: str) -> bool:
    """Whether a `MIDDLEWARE` entry names the class `base` or a subclass of it.

    An entry or a base that does not import still matches by its dotted path.
    """
    if not isinstance(entry, str):
        return False
    if entry == base:
        return True
    found = _middleware_class(entry)
    parent = _middleware_class(base)
    return found is not None and parent is not None and issubclass(found, parent)


def middleware_listed(middleware: Iterable[object], base: str) -> bool:
    """Whether `middleware` lists the class `base` or a subclass of it."""
    return any(is_middleware(entry, base) for entry in middleware)


def child_nodelists(node: Node) -> Iterator[NodeList]:
    """Yield each child node list a node declares on `child_nodelists`."""
    for attr in node.child_nodelists:
        nodelist = getattr(node, attr, None)
        if isinstance(nodelist, NodeList):
            yield nodelist


def redirect_or_fallback(
    target: str, fallback: str, *, status: int = 302
) -> HttpResponseRedirect:
    """Redirect to `target`, or to `fallback` when Django refuses it as a Location.

    A handler has already run by now, so a target past the length cap is not a 400.
    """
    with suppress(DisallowedRedirect):
        return HttpResponseRedirect(target, status=status)
    return HttpResponseRedirect(fallback, status=status)
