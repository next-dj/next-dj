from pathlib import Path

import pytest
from django.test import override_settings

from next.pages.checks.walk import tree_walk
from tests.support import file_router_config_entry, routed, write_page
from tests.support.trees import NAMESPACED_URLCONF


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


def _targets(root: Path | None = None) -> list[tuple[str, str]]:
    return sorted(
        (hit.root.path.name, hit.target_trail)
        for hit in tree_walk().intercepts
        if root is None or hit.root.path == root
    )


class TestRouterMirror:
    """The checks walk enters the trees as the routers that report them do."""

    def test_a_router_over_a_nested_tree_does_not_hide_it_from_another(
        self, root: Path
    ) -> None:
        inner = root / "inner"
        write_page(root, "inner/photo")
        write_page(root, "inner/(.)photo", body="<p/>")
        outer = file_router_config_entry(pages_dir=root)
        nested = file_router_config_entry(pages_dir=inner)

        with override_settings(
            ROOT_URLCONF=NAMESPACED_URLCONF,
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [outer, nested]},
        ):
            found = _targets()

        assert found == [("inner", "photo"), ("pages", "inner/photo")]

    def test_a_tree_nested_in_another_of_one_router_is_left_to_its_own_walk(
        self, root: Path
    ) -> None:
        inner = root / "inner"
        write_page(root, "inner/photo")
        write_page(root, "inner/(.)photo", body="<p/>")

        with routed(root, inner):
            found = _targets()

        assert found == [("inner", "photo")]

    def test_a_symlinked_alias_of_a_directory_is_walked_under_its_own_trail(
        self, root: Path
    ) -> None:
        write_page(root, "a/photo")
        write_page(root, "a/(.)photo", body="<p/>")
        (root / "b").symlink_to(root / "a", target_is_directory=True)

        with routed(root):
            found = _targets()

        assert found == [("pages", "a/photo"), ("pages", "b/photo")]

    def test_a_tree_two_routers_share_is_walked_once(self, root: Path) -> None:
        write_page(root, "photo")
        write_page(root, "(.)photo", body="<p/>")
        entry = file_router_config_entry(pages_dir=root)

        with override_settings(
            ROOT_URLCONF=NAMESPACED_URLCONF,
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry, dict(entry)]},
        ):
            found = _targets()

        assert found == [("pages", "photo")]

    def test_a_failed_router_manager_walks_nothing(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"PAGE_BACKENDS": [{"BACKEND": "no.such.Router"}]}
        ):
            assert tree_walk().intercepts == ()
