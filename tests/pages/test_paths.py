from pathlib import Path

from next.pages.paths import clear_page_path_info, forget_page_path_info, page_path_info
from next.utils import MAX_ANCESTOR_WALK_DEPTH


class TestPagePathInfo:
    """The path facts one page render needs, read from disk once."""

    def test_a_sibling_template_djx_becomes_the_template_path(self, tmp_path) -> None:
        """A page next to ``template.djx`` reports the template as its path."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        (tmp_path / "template.djx").write_text("<p>body</p>")

        assert page_path_info(page_file).template_path == str(tmp_path / "template.djx")

    def test_without_a_template_djx_the_page_is_its_own_template_path(
        self, tmp_path
    ) -> None:
        """A page with no sibling template reports itself."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")

        assert page_path_info(page_file).template_path == str(page_file)

    def test_the_module_path_is_resolved(self, tmp_path) -> None:
        """The module path is the resolved form of the page path."""
        page_file = tmp_path / "sub" / ".." / "page.py"
        (tmp_path / "sub").mkdir()
        (tmp_path / "page.py").write_text("x = 1")

        assert page_path_info(page_file).module_path == str(
            (tmp_path / "page.py").resolve()
        )

    def test_the_chain_starts_at_the_page_itself(self, tmp_path) -> None:
        """The nearest candidate ancestor is the page under inspection."""
        leaf = tmp_path / "leaf"
        leaf.mkdir()
        page_file = leaf / "page.py"

        ancestors = page_path_info(page_file).ancestors

        assert ancestors[0] == page_file
        assert ancestors[1] == tmp_path / "page.py"

    def test_the_chain_stops_at_the_filesystem_root(self, tmp_path) -> None:
        """The last candidate sits directly under the root, never on it."""
        page_file = tmp_path / "page.py"

        ancestors = page_path_info(page_file).ancestors

        assert ancestors[-1].parent.parent == Path(page_file.anchor)

    def test_the_chain_is_bounded_by_the_walk_depth(self, tmp_path) -> None:
        """A tree deeper than the cap contributes no more than the cap."""
        deep = tmp_path
        for i in range(MAX_ANCESTOR_WALK_DEPTH + 6):
            deep = deep / f"d{i}"
            deep.mkdir()

        assert (
            len(page_path_info(deep / "page.py").ancestors) == MAX_ANCESTOR_WALK_DEPTH
        )


class TestPagePathInfoMemo:
    """The facts are memoised until something drops them."""

    def test_a_second_read_returns_the_memoised_object(self, tmp_path) -> None:
        """Two reads of one path hand back the same instance."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")

        assert page_path_info(page_file) is page_path_info(page_file)

    def test_forgetting_one_page_leaves_the_others(self, tmp_path) -> None:
        """The targeted drop rebuilds one entry and keeps the rest."""
        first = tmp_path / "a" / "page.py"
        second = tmp_path / "b" / "page.py"
        first.parent.mkdir()
        second.parent.mkdir()
        kept = page_path_info(second)
        dropped = page_path_info(first)

        forget_page_path_info(first)

        assert page_path_info(first) is not dropped
        assert page_path_info(second) is kept

    def test_clearing_drops_every_page(self, tmp_path) -> None:
        """The full drop rebuilds every entry."""
        page_file = tmp_path / "page.py"
        first = page_path_info(page_file)

        clear_page_path_info()

        assert page_path_info(page_file) is not first

    def test_a_rebuild_sees_a_template_djx_created_after_the_first_read(
        self, tmp_path
    ) -> None:
        """Nothing is cached across the drop, so the new sibling is picked up."""
        page_file = tmp_path / "page.py"
        page_file.write_text("x = 1")
        assert page_path_info(page_file).template_path == str(page_file)

        (tmp_path / "template.djx").write_text("<p>body</p>")
        forget_page_path_info(page_file)

        assert page_path_info(page_file).template_path == str(tmp_path / "template.djx")
