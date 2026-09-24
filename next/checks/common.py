"""Shared helpers used by per-subpackage system-check modules.

The discovery names travel on from `next.discovery`, so one import serves a check
module, and the shared unknown-key probe here owns `next.E035`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error

from next.conf.imports import import_class_cached
from next.discovery import (
    PageRootsError,
    discover_page_registrations,
    first_visit,
    get_page_roots,
    get_pages_directories,
    get_router_manager,
    iter_page_tree_component_folders,
    iter_scanned_page_pairs,
    page_tree_skip_names,
    read_page_roots,
    reset_router_manager_cache,
)


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class RegistrationSubject:
    """The wording one context decorator uses in its registration-file check."""

    decorator: str
    anchor_name: str
    render: str
    code: str


def registration_file_errors(
    subject: RegistrationSubject,
    *,
    registrations: dict[Path, tuple[str, ...]],
    misattributed: Iterable[tuple[Path, Path, str]],
) -> list[CheckMessage]:
    """Report registrations that no render of the intended file ever collects.

    A registration keys on the file declaring the callable, so decorating an imported
    helper binds it to a non-anchor file, or to another anchor with a different URL.
    """
    records = sorted(misattributed, key=_by_paths)
    errors = _cross_file_errors(subject, records)
    errors.extend(_dead_file_errors(subject, registrations, _names_by_file(records)))
    return errors


def import_backend_class(dotted_path: str) -> type[Any]:
    """Import a dotted backend path, folding any import-time failure into ImportError.

    A backend module runs arbitrary code at import, and a check that lets it
    raise takes the whole run down instead of reporting one error.
    """
    try:
        return import_class_cached(dotted_path)
    except Exception as exc:
        msg = f"{dotted_path} raised {type(exc).__name__}: {exc}"
        raise ImportError(msg) from exc


def _names_by_file(records: list[tuple[Path, Path, str]]) -> dict[Path, set[str]]:
    """Group the misattributed names by the file they landed on."""
    grouped: dict[Path, set[str]] = {}
    for _registered_from, declared_in, name in records:
        grouped.setdefault(declared_in, set()).add(name)
    return grouped


def _cross_file_errors(
    subject: RegistrationSubject, records: list[tuple[Path, Path, str]]
) -> list[CheckMessage]:
    """Report each registration that landed on a file other than the one running it."""
    errors: list[CheckMessage] = []
    for registered_from, declared_in, name in records:
        errors.append(
            Error(
                f"{registered_from} runs {subject.decorator} on {name}, declared in "
                f"{declared_in}, so the registration binds to {declared_in} and no "
                f"{subject.render} of {registered_from} collects it. Declare the "
                f"callable in {registered_from}, wrapping the shared helper.",
                obj=str(registered_from),
                id=subject.code,
            )
        )
    return errors


def _dead_file_errors(
    subject: RegistrationSubject,
    registrations: dict[Path, tuple[str, ...]],
    already_reported: dict[Path, set[str]],
) -> list[CheckMessage]:
    """Report registrations sitting on a file the renderer never looks at.

    A name in `already_reported` is left out, because the cross-file report named it.
    """
    errors: list[CheckMessage] = []
    for file_path in sorted(registrations, key=str):
        if file_path.name == subject.anchor_name:
            continue
        unreported = set(registrations[file_path]) - already_reported.get(
            file_path, set()
        )
        if not unreported:
            continue
        names = ", ".join(sorted(unreported))
        errors.append(
            Error(
                f"{file_path} registers {subject.decorator} callables ({names}) but "
                f"is not a {subject.anchor_name}, so no {subject.render} collects "
                f"them. Declare the callable in the {subject.anchor_name} that "
                "needs it, wrapping this helper.",
                obj=str(file_path),
                id=subject.code,
            )
        )
    return errors


def _by_paths(record: tuple[Path, Path, str]) -> tuple[str, str, str]:
    """Order misattribution records so the report is stable across runs."""
    registered_from, declared_in, name = record
    return (str(registered_from), str(declared_in), name)


def errors_for_unknown_keys(
    config: dict[str, Any], *, allowed: frozenset[str], prefix: str
) -> list[CheckMessage]:
    """Return an `Error` list when `config` contains keys outside `allowed`."""
    unknown = sorted(k for k in config if k not in allowed)
    if not unknown:
        return []
    unknown_fmt = ", ".join(repr(k) for k in unknown)
    allowed_fmt = ", ".join(sorted(allowed))
    return [
        Error(
            f"{prefix} has unknown keys {unknown_fmt}. Allowed keys are {allowed_fmt}.",
            obj=settings,
            id="next.E035",
        )
    ]


__all__ = [
    "PageRootsError",
    "RegistrationSubject",
    "discover_page_registrations",
    "errors_for_unknown_keys",
    "first_visit",
    "get_page_roots",
    "get_pages_directories",
    "get_router_manager",
    "import_backend_class",
    "iter_page_tree_component_folders",
    "iter_scanned_page_pairs",
    "page_tree_skip_names",
    "read_page_roots",
    "registration_file_errors",
    "reset_router_manager_cache",
]
