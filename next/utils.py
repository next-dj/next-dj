"""Small reusable helpers shared across framework modules."""

from __future__ import annotations

import inspect
from pathlib import Path


def caller_source_path(  # noqa: C901, PLR0912
    *,
    back_count: int = 1,
    max_walk: int = 15,
    skip_while_filename_endswith: tuple[str, ...] | None = None,
    skip_framework_file: tuple[str, str] | None = None,
) -> Path:
    """Resolve ``Path`` of the caller module's ``__file__`` for decorator registration.

    ``back_count``: steps upward before scanning (e.g. past the decorator wrapper).

    **Pages/forms mode:** pass ``skip_while_filename_endswith``
    (e.g. ``("pages.py",)``).
    Scan frames until ``__file__`` is missing or no longer ends with one of these
    suffixes, then return that path.

    **Components mode:** pass ``skip_framework_file`` as ``(basename, parent_dir_name)``
    e.g. ``("components.py", "next")``. Only ``str`` paths ending in ``.py`` are
    considered; the framework module path (resolved) is skipped.
    """
    if skip_while_filename_endswith is not None and skip_framework_file is not None:
        msg = "Specify only one of skip_while_filename_endswith or skip_framework_file"
        raise ValueError(msg)
    if skip_while_filename_endswith is None and skip_framework_file is None:
        msg = "Specify skip_while_filename_endswith or skip_framework_file"
        raise ValueError(msg)

    frame = inspect.currentframe()
    err_plain = "Could not determine caller file path"
    err_components = f"{err_plain}: no __file__ in caller frames"

    for _ in range(back_count):
        if not frame or not frame.f_back:
            raise RuntimeError(err_plain)
        frame = frame.f_back

    if skip_while_filename_endswith is not None:
        suffixes = skip_while_filename_endswith
        for _ in range(max_walk):
            if not frame:
                break
            raw = frame.f_globals.get("__file__")
            if raw and isinstance(raw, str):
                if any(raw.endswith(sfx) for sfx in suffixes):
                    frame = frame.f_back
                    continue
                return Path(raw)
            frame = frame.f_back
        raise RuntimeError(err_plain)

    base, parent = skip_framework_file  # type: ignore[misc]
    for _ in range(max_walk):
        if not frame:
            break
        raw = frame.f_globals.get("__file__")
        if isinstance(raw, str) and raw.endswith(".py"):
            path = Path(raw).resolve()
            if path.name == base and path.parent.name == parent:
                frame = frame.f_back
                continue
            return path
        frame = frame.f_back
    raise RuntimeError(err_components)
