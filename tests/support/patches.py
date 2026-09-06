from __future__ import annotations

import copy
import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from next.deps import provider_registry, resolver
from next.static import default_kinds, default_placeholders
from next.static.discovery import default_stems
from tests.support.helpers import next_framework_settings_for_checks


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


def _advance_version(registry: object, *, reached: int = 0) -> None:
    """Leave the version of `registry` past every generation it has carried.

    Two registry states sharing a version would make a genuinely stale plan
    read fresh, so a restore rolls the state back and the counter forward.
    `reached` is the version a restore is about to rewind past.
    """
    registry._version = max(registry.version, reached) + 1


@contextmanager
def restored_provider_registry() -> Generator[None, None, None]:
    """Put the provider registry and the singleton's provider list back afterwards.

    A provider class declared inside a test registers itself for the whole
    process, so the class list goes back to what the body found and the
    singleton is left to resync from a version it has never seen. The
    instantiated providers go back too, because the rebuild that follows
    republishes them and a reader that never resolves would see the test's.
    """
    classes = list(provider_registry)
    head = list(resolver._head)
    tail = list(resolver._tail)
    auto = list(resolver._auto)
    suppressed = set(resolver._suppressed)
    try:
        yield
    finally:
        provider_registry.replace(classes)
        resolver._head[:] = head
        resolver._tail[:] = tail
        resolver._auto[:] = auto
        resolver._suppressed = suppressed
        resolver._registry_seen = -1
        resolver._rebuild()


@contextmanager
def bound_dependency(
    name: str, provider: Callable[..., object]
) -> Generator[None, None, None]:
    """Bind `Depends(name)` on the singleton to `provider` itself for the block.

    `override_dependency` wraps a value in a constant lambda, which hides the
    callable a test counts calls on or hands parameters to.
    """
    previous = resolver.get_dependency(name)
    resolver.register_dependency(name, provider)
    try:
        yield
    finally:
        if previous is None:
            resolver.unregister_dependency(name)
        else:
            resolver.register_dependency(name, previous)


@contextmanager
def restored_static_registries() -> Generator[None, None, None]:
    """Put the stem, kind, and slot registries back the way the body found them.

    All three are process globals whose generation every asset plan compares
    against, so a test teaching the framework a new shape puts them back and
    `_advance_version` keeps the counter moving forward.
    """
    registries = (default_stems, default_kinds, default_placeholders)
    saved = [copy.deepcopy(registry.__dict__) for registry in registries]
    try:
        yield
    finally:
        for registry, state in zip(registries, saved, strict=True):
            reached = registry.version
            registry.__dict__.clear()
            registry.__dict__.update(state)
            _advance_version(registry, reached=reached)


@contextmanager
def importable_dir(directory: Path) -> Generator[None, None, None]:
    """Put `directory` on `sys.path` and drop the modules imported from it."""
    before = set(sys.modules)
    sys.path.insert(0, str(directory))
    try:
        yield
    finally:
        sys.path.remove(str(directory))
        for name in set(sys.modules) - before:
            del sys.modules[name]


@contextmanager
def patch_checks_router_manager(
    *, pages_directory: Path
) -> Generator[tuple[MagicMock, MagicMock, MagicMock], None, None]:
    """Point the check seams at one real pages directory through a stub manager."""
    mock_mgr = MagicMock()
    mock_router = MagicMock()
    mock_mgr.backends = (mock_router,)
    mock_router.components_folder_name.return_value = None
    with (
        patch("next.pages.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch("next.urls.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch(
            "next.checks.common.get_pages_directories", return_value=[pages_directory]
        ) as mock_get_pages_dirs,
    ):
        yield mock_mgr, mock_router, mock_get_pages_dirs


@contextmanager
def patch_checks_router_manager_with_routers(
    *, routers: list[object]
) -> Generator[MagicMock, None, None]:
    """Patch `get_router_manager` so the manager exposes the given routers list."""
    mock_mgr = MagicMock()
    mock_mgr.backends = tuple(routers)
    with (
        patch("next.pages.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch("next.partial.checks.get_router_manager", return_value=(mock_mgr, [])),
        patch("next.urls.checks.get_router_manager", return_value=(mock_mgr, [])),
    ):
        yield mock_mgr


@contextmanager
def patch_checks_components_manager(*fake_backends) -> Generator[MagicMock, None, None]:
    """Patch components-check settings and `ComponentsManager` with fake backends."""
    mock_ns = next_framework_settings_for_checks(
        backends=[
            {
                "BACKEND": "next.components.FileComponentsBackend",
                "DIRS": [],
                "COMPONENTS_DIR": "_components",
            }
        ]
    )
    mock_manager = MagicMock()
    mock_manager.backends = tuple(fake_backends)
    with (
        patch("next.components.checks.next_framework_settings", mock_ns),
        patch(
            "next.components.checks.get_components_manager", return_value=mock_manager
        ),
    ):
        yield mock_manager
