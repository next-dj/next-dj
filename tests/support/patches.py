from __future__ import annotations

import copy
import sys
from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from django.contrib.staticfiles.storage import StaticFilesStorage

from next.deps import provider_registry, resolver
from next.static import default_kinds, default_placeholders
from next.static.discovery import default_stems
from tests.support.components import write_colocated_component_assets
from tests.support.helpers import next_framework_settings_stand_in


if TYPE_CHECKING:
    from collections.abc import (
        Callable,
        Generator,
        Iterable,
        Mapping,
        Set as AbstractSet,
    )
    from pathlib import Path


def _advance_version(registry: object, *, reached: int = 0) -> None:
    """Leave the version of `registry` past every generation it has carried.

    Two registry states sharing a version would let a stale plan read as
    fresh, so a restore rolls the state back and the counter forward.
    """
    registry._version = max(registry.version, reached) + 1


@contextmanager
def restored_provider_registry() -> Generator[None, None, None]:
    """Put the provider registry and the singleton's provider list back afterwards.

    A provider class declared in a test registers itself for the whole process, so this
    restores both the class list and the singleton's providers.
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

    These are process globals an asset plan's generation check compares
    against, so `_advance_version` bumps the counter forward after restoring.
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
def patched_watch_sources(
    *,
    pages: Iterable[Path] = (),
    templates: AbstractSet[Path] = frozenset(),
    layouts: AbstractSet[Path] = frozenset(),
    components: AbstractSet[Path] = frozenset(),
) -> Generator[None, None, None]:
    """Answer the four watch seams `next.static.finders` reads from one call.

    Leaving one seam unpatched leaks the real project tree into the caller's assertions,
    and a named component folder is written because no page-tree fixture builds one.
    """
    write_colocated_component_assets(components)
    with (
        patch(
            "next.static.finders.get_pages_directories_for_watch",
            return_value=list(pages),
        ),
        patch(
            "next.static.finders.get_template_djx_paths_for_watch",
            return_value=set(templates),
        ),
        patch(
            "next.static.finders.get_layout_djx_paths_for_watch",
            return_value=set(layouts),
        ),
        patch(
            "next.static.finders.get_component_paths_for_watch",
            return_value=set(components),
        ),
    ):
        yield


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


# Every checks submodule that binds `get_router_manager` at import time. The name is
# read through the module that imported it, so patching the package misses all of them.
PAGES_ROUTER_MANAGER_TARGETS: tuple[str, ...] = (
    "next.pages.checks.contexts.get_router_manager",
    "next.pages.checks.layouts.get_router_manager",
    "next.pages.checks.modules.get_router_manager",
    "next.pages.checks.structure.get_router_manager",
)
PARTIAL_ROUTER_MANAGER_TARGETS: tuple[str, ...] = (
    "next.partial.checks.pages.get_router_manager",
    "next.partial.checks.templates.get_router_manager",
)
URLS_ROUTER_MANAGER_TARGETS: tuple[str, ...] = ("next.urls.checks.get_router_manager",)


@contextmanager
def patched_router_manager(
    *targets: str, manager: MagicMock
) -> Generator[None, None, None]:
    """Answer `get_router_manager` with `manager` at each named binding."""
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target, return_value=(manager, [])))
        yield


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
        patched_router_manager(
            *PAGES_ROUTER_MANAGER_TARGETS,
            *URLS_ROUTER_MANAGER_TARGETS,
            manager=mock_mgr,
        ),
        patch(
            "next.discovery.get_pages_directories", return_value=[pages_directory]
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
    with patched_router_manager(
        *PAGES_ROUTER_MANAGER_TARGETS,
        *PARTIAL_ROUTER_MANAGER_TARGETS,
        *URLS_ROUTER_MANAGER_TARGETS,
        manager=mock_mgr,
    ):
        yield mock_mgr


@contextmanager
def patch_checks_components_manager(*fake_backends) -> Generator[MagicMock, None, None]:
    """Patch components-check settings and `ComponentsManager` with fake backends."""
    mock_ns = next_framework_settings_stand_in(
        COMPONENT_BACKENDS=[
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


@contextmanager
def static_names_resolved_by(
    urls: Mapping[str, str],
) -> Generator[MagicMock, None, None]:
    """Answer `staticfiles_storage.url` from a mapping and miss like a manifest does.

    A name the mapping does not list raises the manifest's own `ValueError`, and the
    patch lands on the class so an `override_settings` inside the block cannot drop it.
    """

    def resolve(name: str) -> str:
        if name not in urls:
            msg = f"The file '{name}' could not be found"
            raise ValueError(msg)
        return urls[name]

    with patch.object(StaticFilesStorage, "url", side_effect=resolve) as url:
        yield url
