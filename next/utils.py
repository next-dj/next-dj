"""Cross-area helpers for paths, `DIRS` entries, page trees, routes, and edit watching.

Everything here sits below the subpackages that share it, so a value object
two of them build travels through this module rather than closing a cycle.
"""

from __future__ import annotations

import enum
import functools
import importlib.machinery
import importlib.util
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, TypeGuard, override
from urllib.parse import unquote_to_bytes

from django.apps import apps
from django.conf import settings
from django.utils.encoding import repercent_broken_unicode
from django.utils.module_loading import import_string
from django.utils.text import slugify

from next.caches import DEFAULT_CACHE_SIZE
from next.errors import InvalidDirsError


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Generator, Iterable, Mapping


logger = logging.getLogger(__name__)


# The bound every ancestor walk shares, so none reaches the filesystem root.
MAX_ANCESTOR_WALK_DEPTH = 64

WEB_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})
"""The URL schemes a page, a head tag or a sitemap may point a crawler at."""


class Unset(enum.Enum):
    """The type of `UNSET`, the one sentinel for a value not read yet."""

    UNSET = enum.auto()


UNSET: Final = Unset.UNSET
"""What a memo holds before its first read, told apart from a `None` it may hold."""


def is_int(value: object) -> TypeGuard[int]:
    """Whether `value` is an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


ROUTE_BRACKET_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\[\[(?P<wild>[^\[\]]+)\]\]|\[(?P<param>[^\[\]]+)\]"
)
"""The bracket segments of a trail, a double-bracket wildcard or a typed parameter."""


def is_dynamic_trail(url_path: str) -> bool:
    """Whether `url_path` carries a bracket segment, so it routes with parameters."""
    return ROUTE_BRACKET_PATTERN.search(url_path) is not None


def normalise_route_name(raw_name: str) -> str:
    """Return the route name a bracket segment maps to, reading `-` as `_`.

    Django accepts only a Python identifier between its angle brackets, and the parser
    and the directory check both read the name through this one rule.
    """
    return raw_name.replace("-", "_")


def decode_url_path(path: str) -> str:
    """Return a percent-encoded URL path decoded the way Django builds `request.path`.

    Decoded only once split from its query, so an encoded `?` stays in its segment.
    """
    return repercent_broken_unicode(unquote_to_bytes(path)).decode()


@functools.lru_cache(maxsize=DEFAULT_CACHE_SIZE)
def resolved_tree(path: Path) -> Path:
    """Return the resolved form of a page tree, memoised across the process.

    A router re-reads the same handful of trees, and resolving one costs more than
    everything else the read pays for, so the LRU bound caps a runaway caller too.
    """
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

    Nanoseconds rather than a float, because a float timestamp rounds two
    writes a filesystem told apart back into one value.
    """
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


class _SourceLoader(importlib.machinery.SourceFileLoader):
    """Validate the cached bytecode of a source on its nanosecond mtime.

    A `.pyc` keyed on whole seconds hands a same-size rewrite the older code.
    """

    @override
    def path_stats(self, path: str) -> Mapping[str, Any]:
        """Return the mtime in nanoseconds, the low bits of which the `.pyc` keeps."""
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
    """One Python file at the top of a page tree, the module it ran to or its failure.

    `stamp` is the mtime it ran at, so a watched process reads it again once it moves.
    """

    path: Path
    module: types.ModuleType | None = None
    error: E | None = None
    stamp: int | None = None

    def stale(self) -> bool:
        """Whether the file moved on disk since it ran, gone or rewritten."""
        return stat_mtime_ns(self.path) != self.stamp


def load_tree_source[E: Exception](
    path: Path, module_name: str, error: Callable[[Path], E]
) -> TreeSource[E] | None:
    """Execute the file at `path` as `module_name`, `None` where no file sits there.

    It runs user code, so any exception is the user's and stays on the source to report.
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
    """A page tree a router routes, with the label a report names it by."""

    path: Path
    label: str


def _app_label_for(path: Path) -> str | None:
    """Return the label of the innermost installed app whose directory holds `path`."""
    resolved = path.resolve()
    holding = [
        (len(app_path.parts), str(config.label))
        for config in apps.get_app_configs()
        if resolved.is_relative_to(app_path := Path(config.path).resolve())
    ]
    return max(holding)[1] if holding else None


def tree_label(path: Path) -> str:
    """Return the stable name of a page tree, its app label or else its directory."""
    label = _app_label_for(path)
    if label is None:
        label = slugify(path.name) or "root"
    return label


def unique_labels(labels: Iterable[str]) -> list[str]:
    """Return one distinct label per label, a repeat suffixed past every label taken.

    A label no other tree shares keeps it, so a suffix never lands on a natural label.
    """
    wanted = list(labels)
    taken = set(wanted)
    unique: list[str] = []
    handed: set[str] = set()
    for label in wanted:
        candidate = label
        number = 1
        while candidate in handed or (candidate != label and candidate in taken):
            number += 1
            candidate = f"{label}-{number}"
        handed.add(candidate)
        unique.append(candidate)
    return unique


def page_roots_shape_error(source: str, roots: list[Any]) -> str | None:
    """Return why `roots` is no list of page trees, or `None` when it is one.

    Both readers of `page_roots` dereference `root.path`, so the rule they
    refuse a third-party backend by lives here with the value object.
    """
    for root in roots:
        if not isinstance(root, PageRoot):
            return (
                f"{source} reported {type(root).__name__} instead of a "
                "next.urls.PageRoot page tree"
            )
        # Widened from the declared `Path`, because a dataclass validates no
        # field and every reader dereferences the value as a path.
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
    """Whether the caches re-read the disk to notice a change under way.

    Autoreload ignores `.djx` edits and new directories, so only a re-read notices
    either, and `DEBUG` is read per call so an override takes effect.
    """
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

    A relative Windows-style entry read on POSIX carries no separator of its own, so
    its last component comes from the forward-slashed text instead of `Path` splitting.
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
        # Resolved before the probe, so a `..` reaching past a directory that
        # does not exist still names the tree the entry means.
        resolved = None if candidate is None else resolved_tree(candidate)
        if resolved is not None and resolved.is_dir():
            path_roots.append(resolved)
        else:
            name = _dir_entry_segment_name(item)
            # An entry that is nothing but separators names no segment, and an
            # empty name would reach `skip_dir_names` as a directory to refuse.
            if name:
                segments.add(name)

    return path_roots, frozenset(segments)


_PAGE_FILES: Final = frozenset({"page.py", "template.djx"})
"""The files that make a directory a page, a `template.djx` alone a virtual one."""


def walk_page_tree(
    tree_root: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    on_skipped_dir: Callable[[Path, Path, str], None] | None = None,
) -> Generator[tuple[str, Path], None, None]:
    """Yield `(url_path, page_file)` for every page under `tree_root`.

    A directory holding a `template.djx` and no `page.py` yields the page file it would
    have, as a virtual page, and `on_skipped_dir` reports every directory refused entry.
    """
    yield from _visit_page_dir(
        tree_root, tree_root, "", frozenset(skip_dir_names), on_skipped_dir
    )


_CONVERTER_RANK: Final[dict[str, int]] = {"int": 0, "uuid": 0, "slug": 1, "path": 3}
"""How much a converter matches, narrowest first, `str` and the bare form at 2.

A converter a project registers is read as `slug`, narrower than `str`.
"""


def _route_rank(name: str) -> tuple[int, int, int, str]:
    """Rank a directory among its siblings, the most specific route first.

    Django tries patterns in order, so a static `about` precedes a `[slug]` that would
    match it too, whatever order the directory read returned. A static segment comes
    first, then a parameter, then a catch-all. Within each, the one with more literal
    text goes first, so `post-[id]` precedes `[slug]`, then the narrower converters,
    so `[uuid:key]` precedes `[str:key]`, and the name settles the rest.
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


def _visit_page_dir(
    current_path: Path,
    tree_root: Path,
    url_path: str,
    skip_dir_names: frozenset[str],
    on_skipped_dir: Callable[[Path, Path, str], None] | None,
) -> Generator[tuple[str, Path], None, None]:
    """Yield the page of one directory, then descend into its route children.

    The page comes first, real or virtual, and the children follow in `_route_rank`
    order, so the patterns come out the same on every file system.
    """
    try:
        with os.scandir(current_path) as scan:
            entries = list(scan)
    except OSError as e:
        logger.debug("Cannot list directory %s: %s", current_path, e)
        return
    has_page = False
    children: list[os.DirEntry[str]] = []
    for entry in entries:
        # The kind rides along with the name the directory read returned, so a
        # tree costs a stat per symlink rather than one per entry it holds.
        if entry.is_dir():
            children.append(entry)
        elif entry.name in _PAGE_FILES:
            has_page = True
    if has_page:
        yield url_path, current_path / "page.py"
    children.sort(key=lambda entry: _route_rank(entry.name))
    for entry in children:
        if entry.name in skip_dir_names:
            if on_skipped_dir is not None:
                on_skipped_dir(Path(entry.path), tree_root, url_path)
            continue
        child_url = f"{url_path}/{entry.name}" if url_path else entry.name
        yield from _visit_page_dir(
            Path(entry.path), tree_root, child_url, skip_dir_names, on_skipped_dir
        )


@functools.cache
def _middleware_class(path: str) -> type | None:
    """Import a `MIDDLEWARE` entry, `None` when it does not import as a class."""
    try:
        found = import_string(path)
    except Exception:  # noqa: BLE001 - a broken entry is Django's to report at startup
        return None
    return found if isinstance(found, type) else None


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


def middleware_index(middleware: Iterable[object], base: str) -> int | None:
    """Return where the first entry `is_middleware` matches sits, `None` for none."""
    for index, entry in enumerate(middleware):
        if is_middleware(entry, base):
            return index
    return None


def middleware_listed(middleware: Iterable[object], base: str) -> bool:
    """Whether `middleware` lists the class `base` or a subclass of it."""
    return middleware_index(middleware, base) is not None
