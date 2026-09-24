"""Cross-area helpers for paths, `DIRS` entries, page trees, and edit watching.

Everything here sits below the subpackages that share it, so a value object
two of them build travels through this module rather than closing a cycle.
"""

from __future__ import annotations

import functools
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_to_bytes

from django.conf import settings
from django.utils.encoding import repercent_broken_unicode

from next.caches import DEFAULT_CACHE_SIZE
from next.errors import InvalidDirsError


if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable


logger = logging.getLogger(__name__)


# The bound every ancestor walk shares, so none reaches the filesystem root.
MAX_ANCESTOR_WALK_DEPTH = 64


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


@dataclass(frozen=True, slots=True)
class PageRoot:
    """A page tree a router routes, with the label a report names it by."""

    path: Path
    label: str


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


def _visit_page_dir(
    current_path: Path,
    tree_root: Path,
    url_path: str,
    skip_dir_names: frozenset[str],
    on_skipped_dir: Callable[[Path, Path, str], None] | None,
) -> Generator[tuple[str, Path], None, None]:
    """Yield the pages of one directory, then descend into its route children."""
    try:
        with os.scandir(current_path) as scan:
            entries = list(scan)
    except OSError as e:
        logger.debug("Cannot list directory %s: %s", current_path, e)
        return
    has_page = False
    has_template = False
    for entry in entries:
        # The kind rides along with the name the directory read returned, so a
        # tree costs a stat per symlink rather than one per entry it holds.
        if entry.is_dir():
            if entry.name in skip_dir_names:
                if on_skipped_dir is not None:
                    on_skipped_dir(Path(entry.path), tree_root, url_path)
                continue
            child_url = f"{url_path}/{entry.name}" if url_path else entry.name
            yield from _visit_page_dir(
                Path(entry.path), tree_root, child_url, skip_dir_names, on_skipped_dir
            )
        elif entry.name == "page.py":
            has_page = True
            yield url_path, Path(entry.path)
        elif entry.name == "template.djx":
            has_template = True

    if has_template and not has_page:
        yield url_path, current_path / "page.py"
