from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

from next.server import get_framework_filesystem_roots_for_linking
from tests.support.backends import file_components_entry


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


class TestFrameworkFilesystemRoots:
    """``get_framework_filesystem_roots_for_linking`` over ``COMPONENT_BACKENDS``."""

    def test_every_dict_entry_contributes_resolved_dirs(
        self, tmp_path: Path, apply_component_backends: Callable[[list[Any]], None]
    ) -> None:
        """Roots come back resolved, deduplicated and sorted."""
        first = tmp_path / "first"
        second = tmp_path / "second"
        first.mkdir()
        second.mkdir()
        apply_component_backends(
            [file_components_entry(second, first), file_components_entry(first)]
        )
        assert get_framework_filesystem_roots_for_linking() == sorted(
            {first.resolve(), second.resolve()}
        )

    def test_a_page_tree_is_taken_as_the_watch_layer_spells_it(
        self, tmp_path: Path, apply_component_backends: Callable[[list[Any]], None]
    ) -> None:
        """The watch layer answers resolved, so a link root pays no second resolve."""
        apply_component_backends([])
        spelling = tmp_path / "site" / ".." / "site"
        with patch(
            "next.server.roots.get_pages_directories_for_watch", return_value=[spelling]
        ):
            assert get_framework_filesystem_roots_for_linking() == [spelling]
