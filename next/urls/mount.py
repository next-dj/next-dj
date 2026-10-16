"""`pages()`, which mounts one application page tree wherever a URLconf includes it."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, override

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from next.utils import resolved_tree

from .backends import FileRouterBackend, is_plain_subdir
from .manager import build_url_resolver, routed_pages, router_manager
from .urlconf import Mount, MountedPatterns


if TYPE_CHECKING:
    from collections.abc import Sequence

    from django.apps import AppConfig
    from django.urls import URLPattern, URLResolver


def _app_config(app: str) -> AppConfig:
    """Return the installed app whose label or dotted name is `app`."""
    for config in apps.get_app_configs():
        if app in {config.label, config.name}:
            return config
    msg = f"pages() names {app!r}, which is neither the label nor the name of an app."
    raise LookupError(msg)


def tree_router() -> FileRouterBackend | None:
    """Return the file router that builds every tree a `pages()` include mounts."""
    return next(
        (
            backend
            for backend in router_manager.backends
            if isinstance(backend, FileRouterBackend)
        ),
        None,
    )


def _tree_patterns(mount: Mount) -> list[URLPattern | URLResolver]:
    """Build the patterns of a mounted tree through the tree router."""
    router = tree_router()
    patterns = [] if router is None else router.generate_tree_urls(mount.path)
    routed_pages.fit(mount, patterns)
    return patterns


class _TreePatterns(MountedPatterns):
    """The patterns of one mounted tree, built on first read and per router version."""

    mount: Mount

    def __init__(self, mount: Mount) -> None:
        """Hold the tree, with no patterns built yet."""
        self.mount = mount
        self._cache: tuple[int, list[URLPattern | URLResolver]] | None = None

    def version_token(self) -> tuple[int]:
        """Return the router version the patterns of the tree are built at."""
        return (router_manager.version,)

    @override
    def _patterns(self) -> list[URLPattern | URLResolver]:
        cache = self._cache
        if cache is not None and cache[0] == router_manager.version:
            return cache[1]
        patterns = _tree_patterns(self.mount)
        self._cache = (router_manager.version, patterns)
        return patterns


class MountedTree(MountedPatterns):
    """The include target of `pages()`, one resolver over the patterns of a tree.

    The resolver is rebuilt per router version, so a reload reaches its reverse map.
    """

    mount: Mount

    def __init__(self, mount: Mount) -> None:
        """Hold the tree, with no resolver built yet."""
        self.mount = mount
        self._slot: tuple[int, tuple[URLPattern | URLResolver, ...]] | None = None

    @override
    def __repr__(self) -> str:
        """Name the app and the subdirectory of the tree."""
        return f"<{type(self).__name__} {self.mount.app}/{self.mount.subdir}>"

    @override
    def _patterns(self) -> tuple[URLPattern | URLResolver, ...]:
        slot = self._slot
        version = router_manager.version
        if slot is None or slot[0] != version:
            resolver = build_url_resolver(_TreePatterns(self.mount))
            slot = self._slot = (version, (resolver,))
        return slot[1]

    @override
    def __len__(self) -> int:
        return 1


def _tree_subdir(app: str, subdir: str | None) -> str:
    """Return the directory `pages()` mounts, by default the tree router's `PAGES_DIR`.

    Only an explicit name must be plain, since the default is the router's own string.
    """
    if subdir is not None:
        if not is_plain_subdir(subdir):
            msg = (
                f"pages({app!r}, {subdir!r}) takes one plain directory name of the app."
            )
            raise ImproperlyConfigured(msg)
        return subdir
    router = tree_router()
    if router is None:
        msg = (
            f"pages({app!r}) mounts the PAGES_DIR of the first file router, and "
            "PAGE_BACKENDS lists none. Pass the directory to mount as subdir."
        )
        raise ImproperlyConfigured(msg)
    return router.pages_dir


def pages(
    app: str, subdir: str | None = None, *, namespace: str | None = None
) -> tuple[Sequence[URLPattern | URLResolver], str, str]:
    """Mount the page tree `subdir` of `app` where a URLconf includes the result.

    The `include()` triple it returns overrides the app's own declaration of that tree.
    """
    config = _app_config(app)
    directory = _tree_subdir(app, subdir)
    tree = Path(config.path) / directory
    if not tree.is_dir():
        call = f"{app!r}" if subdir is None else f"{app!r}, {subdir!r}"
        msg = f"pages({call}) names {tree}, which is no directory."
        raise ImproperlyConfigured(msg)
    mount = Mount(
        app=config.label,
        name=config.name,
        subdir=directory,
        path=resolved_tree(tree),
        namespace=config.label if namespace is None else namespace,
    )
    return MountedTree(mount), config.label, mount.namespace


__all__ = ["MountedTree", "pages", "tree_router"]
