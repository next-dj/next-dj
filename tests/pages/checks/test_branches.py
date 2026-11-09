from pathlib import Path

from next.pages.checks.branches import empty_branches
from tests.support import RootPagesRouter, patch_checks_router_manager_with_routers


class TestEmptyBranches:
    """`empty_branches` walks only the trees that exist on disk."""

    def test_a_missing_tree_root_holds_nothing(self, tmp_path: Path) -> None:
        routers = [RootPagesRouter([tmp_path / "missing"])]
        with patch_checks_router_manager_with_routers(routers=routers):
            assert list(empty_branches(())) == []
