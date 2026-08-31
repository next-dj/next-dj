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
    from collections.abc import Callable, Generator, Iterable


logger = logging.getLogger(__name__)


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
    """Whether the composition caches stat their sources to notice an edit.

    Autoreload leaves `.djx` alone, so under `DEBUG` the stat is the only
    thing making an edit visible. Read per call, so an override takes effect.
    """
    return bool(settings.DEBUG)


def _classify_one_dir_entry(
    item: Path, base_dir: Path | None
) -> tuple[str, Path | str | None]:
    if item.is_absolute():
        if item.exists() and item.is_dir():
            return "path", item
        return "segment", item.name

    s = str(item).replace("\\", "/")
    if "/" in s:
        if base_dir is not None:
            cand = (base_dir / item).resolve()
            if cand.exists() and cand.is_dir():
                return "path", cand
        return "segment", Path(s).name or None

    if base_dir is not None:
        cand = base_dir / item
        if cand.exists() and cand.is_dir():
            return "path", cand.resolve()

    return "segment", item.name


def classify_dirs_entries(
    entries: list[Any] | tuple[Any, ...] | None, base_dir: Path | None
) -> tuple[list[Path], frozenset[str]]:
    """Split ``DIRS`` into directory roots and URL segment names (file router)."""
    path_roots: list[Path] = []
    segments: set[str] = set()
    if not entries:
        return path_roots, frozenset()

    for raw in entries:
        if raw is None:
            continue
        item = Path(raw) if not isinstance(raw, Path) else raw
        s = str(item)
        if not s or s == ".":
            continue

        kind, value = _classify_one_dir_entry(item, base_dir)
        if kind == "path" and isinstance(value, Path):
            path_roots.append(value.resolve())
        elif kind == "segment" and isinstance(value, str) and value:
            segments.add(value)

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
