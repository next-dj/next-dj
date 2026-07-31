"""Shared helpers used by per-subpackage system-check modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from weakref import WeakKeyDictionary

from django.apps import apps
from django.conf import settings
from django.core.checks import CheckMessage, Error

from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from next.components.manager import ComponentsManager
    from next.urls import FileRouterBackend, RouterBackend, RouterManager


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

    A registration keys on the file declaring the callable, so decorating an
    imported helper binds it either to a file that is no anchor at all, or to
    another anchor file whose render answers a different URL.
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

    A name in `already_reported` is left out, because the cross-file report
    has named that callable and its fix already.
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


# One manager per `manage.py check` run instead of rescanning the page and
# component trees for every registered check.
_ROUTER_MANAGER_CACHE: dict[
    str, tuple[RouterManager | None, list[CheckMessage]] | None
] = {"value": None}
_COMPONENTS_MANAGER_CACHE: dict[str, ComponentsManager | None] = {"value": None}

# Scan pairs keyed by the router object. A weak key ties each entry to its
# router's lifetime, so a reused `id` can never alias a stale entry.
_SCANNED_PAIRS_CACHE: WeakKeyDictionary[RouterBackend, list[tuple[str, Path]]] = (
    WeakKeyDictionary()
)


def get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Return a per-run cached `RouterManager` or initialisation errors.

    The cache is dropped only on `settings_reloaded` (a `NEXT_FRAMEWORK` change,
    which `override_settings` triggers) or an explicit `reset_check_caches`, so
    changes to other router inputs (for example `INSTALLED_APPS`) need a reset.
    """
    cached = _ROUTER_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    # next.urls.checks imports next.checks.common, so the manager import is
    # deferred here to break the next.checks.common <-> next.urls cycle.
    from next.urls import RouterManager  # noqa: PLC0415

    result: tuple[RouterManager | None, list[CheckMessage]]
    try:
        router_manager = RouterManager()
        router_manager.reload()
    except (ImportError, AttributeError) as e:
        error = Error(
            f"Error initializing router manager: {e}", obj=settings, id="next.E007"
        )
        result = (None, [error])
    else:
        result = (router_manager, [])
    _ROUTER_MANAGER_CACHE["value"] = result
    return result


def reset_router_manager_cache(**kwargs) -> None:
    """Drop the cached `RouterManager` and its scan pairs for the next run.

    Scan pairs are meaningless without the manager that kept their routers
    alive, so both slots clear on the same reset contour.
    """
    _ROUTER_MANAGER_CACHE["value"] = None
    _SCANNED_PAIRS_CACHE.clear()


def get_components_manager() -> ComponentsManager:
    """Return a per-run cached `ComponentsManager` with its config loaded.

    Invalidated like `get_router_manager`.
    """
    cached = _COMPONENTS_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    # next.components imports next.conf, which imports next.checks.common during
    # app setup, so the manager import is deferred here to break that cycle.
    from next.components.manager import ComponentsManager  # noqa: PLC0415

    manager = ComponentsManager()
    manager._reload_config()
    _COMPONENTS_MANAGER_CACHE["value"] = manager
    return manager


def reset_components_manager_cache(**kwargs) -> None:
    """Drop the cached `ComponentsManager` so the next check run rebuilds it."""
    _COMPONENTS_MANAGER_CACHE["value"] = None


settings_reloaded.connect(reset_router_manager_cache)
settings_reloaded.connect(reset_components_manager_cache)


def get_first_root_pages_path(file_router: FileRouterBackend) -> Path | None:
    """Return the first entry from `_get_root_pages_paths` when defined."""
    if not hasattr(file_router, "_get_root_pages_paths"):
        return None
    root_paths = file_router._get_root_pages_paths()
    return root_paths[0] if root_paths else None


def get_first_app_pages_dir(file_router: FileRouterBackend) -> Path | None:
    """Return the first existing app pages directory, or `None`."""
    for app_config in apps.get_app_configs():
        potential = Path(app_config.path) / str(file_router.pages_dir)
        if potential.exists():
            return potential
    return None


def get_pages_directory(router: RouterBackend) -> Path | None:
    """Return one representative pages root directory for scanning checks."""
    if not hasattr(router, "pages_dir"):
        return None
    file_router = cast("FileRouterBackend", router)
    if file_router.app_dirs:
        return get_first_app_pages_dir(file_router) or get_first_root_pages_path(
            file_router
        )
    p = Path(str(file_router.pages_dir))
    return get_first_root_pages_path(file_router) or (p if p.exists() else None)


def iter_scanned_page_pairs(router: RouterBackend) -> Iterator[tuple[str, Path]]:
    """Yield pairs from `_scan_pages_directory` when the router is scannable."""
    if not hasattr(router, "_scan_pages_directory"):
        return
    pages_dir = get_pages_directory(router)
    if not pages_dir:
        return
    pairs = _SCANNED_PAIRS_CACHE.get(router)
    if pairs is None:
        pairs = list(router._scan_pages_directory(pages_dir))
        _SCANNED_PAIRS_CACHE[router] = pairs
    yield from pairs


__all__ = [
    "errors_for_unknown_keys",
    "get_components_manager",
    "get_first_app_pages_dir",
    "get_first_root_pages_path",
    "get_pages_directory",
    "get_router_manager",
    "import_backend_class",
    "iter_scanned_page_pairs",
    "reset_components_manager_cache",
    "reset_router_manager_cache",
]
