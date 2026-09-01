"""Cross-area helpers for paths, page trees, edit watching, and declaration sites.

Everything here sits below the subpackages that share it, so a value
object two of them build travels through this module rather than closing
an import cycle between them.
"""

from __future__ import annotations

import functools
import inspect
import logging
from dataclasses import dataclass
from pathlib import Path
from types import CodeType
from typing import TYPE_CHECKING, Any, NamedTuple

from django.conf import settings


if TYPE_CHECKING:
    from collections import OrderedDict
    from collections.abc import Callable, Generator, Iterable


logger = logging.getLogger(__name__)


# The bound every ancestor walk shares, so none reaches the filesystem root.
MAX_ANCESTOR_WALK_DEPTH = 64

_RESOLVED_TREES_MAX_SIZE = 2048


@functools.lru_cache(maxsize=_RESOLVED_TREES_MAX_SIZE)
def resolved_tree(path: Path) -> Path:
    """Return the resolved form of a page tree, memoised across the process.

    A router reports the same handful of trees on every read, and resolving one
    costs more than everything else the read pays for it. The bound drops the
    least recently read, so a router free to name a new tree per read grows the
    memo no further while the steady set of a project stays in it.
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


def store_bounded[K, V](cache: OrderedDict[K, V], key: K, value: V, size: int) -> None:
    """Make `key` the freshest entry of a bounded cache and evict the stalest.

    The write lands before the reorder, so a key already held never goes
    missing for a concurrent reader. No caller holds a lock, so the two steps
    after it tolerate another thread evicting the very key being written, which
    costs a rebuild rather than an error out of a render.
    """
    cache[key] = value
    try:
        cache.move_to_end(key)
        if len(cache) > size:
            cache.popitem(last=False)
    except KeyError:
        # The key is gone, so there is nothing left to reorder and the eviction
        # that took it already brought the cache back inside the bound.
        return


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

    Autoreload leaves `.djx` alone and restarts for no directory that appears,
    so under `DEBUG` reading again is the only thing making either visible.
    Read per call, so an override takes effect.
    """
    return bool(settings.DEBUG)


def _dir_entry_candidate(item: Path, base_dir: Path | None) -> Path | None:
    """Return the path a ``DIRS`` entry could name, before it is resolved.

    A relative entry names one only against ``BASE_DIR``, so without one the
    entry can only be a URL segment.
    """
    if item.is_absolute():
        return item
    return None if base_dir is None else base_dir / item


def _dir_entry_segment_name(item: Path) -> str:
    """Return the URL segment name a ``DIRS`` entry that names no tree carries.

    A relative Windows-style entry read on POSIX carries no separator of its
    own, so its last component comes from the forward-slashed text instead. An
    absolute entry already separates, and a backslash in it is part of a name.
    """
    if item.is_absolute():
        return item.name
    text = str(item).replace("\\", "/")
    return Path(text).name if "/" in text else item.name


def _iter_dir_entries(
    entries: list[Any] | tuple[Any, ...] | None,
) -> Generator[Path, None, None]:
    """Yield every ``DIRS`` entry that names anything, as a path."""
    for raw in entries or ():
        if raw is None:
            continue
        item = Path(raw) if not isinstance(raw, Path) else raw
        # `Path("")` spells itself ".", so the one test covers the empty entry.
        if str(item) != ".":
            yield item


def classify_dirs_entries(
    entries: list[Any] | tuple[Any, ...] | None, base_dir: Path | None
) -> tuple[list[Path], frozenset[str]]:
    """Split ``DIRS`` into directory roots and URL segment names (file router)."""
    path_roots: list[Path] = []
    segments: set[str] = set()
    for item in _iter_dir_entries(entries):
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

    A directory holding a `template.djx` and no `page.py` yields the page file
    it would have, because the router routes it as a virtual page.
    `on_skipped_dir` receives every directory the walk refuses to enter, which
    is how the router registers the component folders it passes.
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
        items = list(current_path.iterdir())
    except OSError as e:
        logger.debug("Cannot list directory %s: %s", current_path, e)
        return
    has_page = False
    has_template = False
    for item in items:
        if item.is_dir():
            if item.name in skip_dir_names:
                if on_skipped_dir is not None:
                    on_skipped_dir(item, tree_root, url_path)
                continue
            dir_name = item.name
            new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
            yield from _visit_page_dir(
                item, tree_root, new_url_path, skip_dir_names, on_skipped_dir
            )
        elif item.name == "page.py":
            has_page = True
            yield url_path, item
        elif item.name == "template.djx":
            has_template = True

    if has_template and not has_page:
        yield url_path, current_path / "page.py"


_CLASS_BODY_MEMBERS: tuple[str, ...] = ("__call__", "__init__")


def _code_filename(func: object) -> str | None:
    """Return the source file behind ``func.__code__``, or ``None`` when it has none."""
    target = func
    if callable(func):
        try:
            target = inspect.unwrap(func)
        except ValueError:
            # A ``__wrapped__`` cycle names no innermost function, so the
            # outermost wrapper answers rather than the registration failing.
            target = func
    code = getattr(target, "__code__", None)
    return code.co_filename if isinstance(code, CodeType) else None


def _class_filename(cls: type) -> str | None:
    """Return the file declaring ``cls``, reading its own body when the module is gone.

    The file router execs a ``page.py`` from a spec it never registers, so
    ``sys.modules`` names no file for classes declared there. A method written
    in the class body still carries the path in its code object.
    """
    try:
        return inspect.getfile(cls)
    except (OSError, TypeError):
        for name in _CLASS_BODY_MEMBERS:
            filename = _code_filename(cls.__dict__.get(name))
            if filename is not None:
                return filename
    return None


def defining_file(obj: object) -> Path:
    """Return the file where ``obj`` was declared, for decorator registration.

    Wrappers stay transparent only while they set ``__wrapped__`` through
    :func:`functools.wraps`, and a class built by ``type()`` inside foreign
    code keeps no link to the file that asked for it. Where ``sys.modules``
    names no file, a code object from the object's own body answers instead.
    """
    if isinstance(obj, functools.partial):
        return defining_file(obj.func)
    if inspect.isclass(obj):
        filename = _class_filename(obj)
    elif callable(obj):
        filename = _code_filename(obj) or _code_filename(type(obj).__call__)
    else:
        filename = None
    if filename is not None:
        return Path(filename)
    msg = (
        f"next.dj could not determine the file where {obj!r} was declared, "
        "so the registration has no page or component to belong to. Declare a "
        "function with 'def' in the file that uses it and decorate that."
    )
    raise TypeError(msg)


def callable_name(obj: object) -> str:
    """Return the name a registration reports for ``obj`` in diagnostics.

    A partial and a callable instance carry no ``__name__``, so the name of
    the wrapped function or of the class stands in for one.
    """
    if isinstance(obj, functools.partial):
        return callable_name(obj.func)
    name = getattr(obj, "__name__", None)
    return name if isinstance(name, str) else type(obj).__name__


class MisattributedContext(NamedTuple):
    """One registration whose callable was declared outside the file running it.

    Both files are kept because a diagnostic has to name the file that
    expected the value and the one the registration landed on.
    """

    registered_from: Path
    declared_in: Path
    name: str


class MisattributionLog:
    """Collect registrations bound to a file other than the one running them."""

    def __init__(self) -> None:
        """Start with no recorded registration."""
        # Keyed by the whole record so a module executed more than once
        # reports one diagnostic rather than one per execution.
        self._records: dict[MisattributedContext, None] = {}

    def record(
        self, registered_from: Path, declared_in: Path, func: Callable[..., Any]
    ) -> None:
        """Note that `func` bound to `declared_in` while `registered_from` ran."""
        entry = MisattributedContext(
            registered_from=registered_from,
            declared_in=declared_in,
            name=callable_name(func),
        )
        self._records[entry] = None

    def entries(self) -> tuple[MisattributedContext, ...]:
        """Return every recorded registration, in the order first seen."""
        return tuple(self._records)

    def clear(self) -> None:
        """Drop every recorded registration."""
        self._records.clear()
