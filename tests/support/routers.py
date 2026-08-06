from __future__ import annotations

from pathlib import Path

from next.urls import PageRoot, RouterBackend


class RootPagesRouter(RouterBackend):
    """Third-party backend that routes root page trees and registers no components."""

    def __init__(self, root_trees: list[Path]) -> None:
        """Store the root page trees this backend serves."""
        self._root_trees = list(root_trees)

    def generate_urls(self) -> list:
        """Contribute no patterns, the checks read the reported trees instead."""
        return []

    def page_roots(self) -> list[PageRoot]:
        """Return every configured root page tree."""
        return [PageRoot(path=tree, label="Root") for tree in self._root_trees]


class RaisingRootsRouter(RouterBackend):
    """Backend whose tree listing raises, the way a database-backed one can."""

    def generate_urls(self) -> list:
        """Contribute no patterns."""
        return []

    def page_roots(self) -> list[PageRoot]:
        """Fail the way third-party code reaching an unavailable source fails."""
        msg = "database is down"
        raise RuntimeError(msg)


class MalformedRootsRouter(RouterBackend):
    """Backend whose tree listing answers the wrong shape rather than raising.

    A plugin with a type slip hands back bare paths instead of `PageRoot`
    entries, which no reader may dereference.
    """

    def __init__(self, trees: list[Path]) -> None:
        """Store the trees this backend reports without their labels."""
        self._trees = list(trees)

    def generate_urls(self) -> list:
        """Contribute no patterns."""
        return []

    def page_roots(self) -> list[Path]:
        """Answer bare paths instead of `PageRoot` entries."""
        return list(self._trees)


class OddComponentsNameRouter(RootPagesRouter):
    """Backend that names its components folder as something other than `str`."""

    def components_folder_name(self) -> str | None:
        """Answer a `Path` where the contract says a name."""
        return Path("widgets")  # type: ignore[return-value]


class RaisingComponentsRouter(RootPagesRouter):
    """Backend that lists its trees but raises when named for its components."""

    def components_folder_name(self) -> str | None:
        """Fail where the watcher asks for the component glob."""
        msg = "components folder unavailable"
        raise RuntimeError(msg)


class SkippingRouter(RootPagesRouter):
    """Backend that refuses directory names of its own while walking its trees."""

    def __init__(self, root_trees: list[Path], skip_names: frozenset[str]) -> None:
        """Store the trees this backend serves and the names its walk refuses."""
        super().__init__(root_trees)
        self._skip_names = skip_names

    def skip_dir_names(self) -> frozenset[str]:
        """Answer the configured skip set."""
        return self._skip_names


class OddSkipNamesRouter(RootPagesRouter):
    """Backend whose skip set is a bare string rather than a collection of names."""

    def skip_dir_names(self) -> frozenset[str]:
        """Answer a `str`, whose characters are no directory names."""
        return "api"  # type: ignore[return-value]


class RaisingSkipNamesRouter(RootPagesRouter):
    """Backend that lists its trees but raises when asked what its walk refuses."""

    def skip_dir_names(self) -> frozenset[str]:
        """Fail where the checks ask for the skip set."""
        msg = "skip names unavailable"
        raise RuntimeError(msg)
