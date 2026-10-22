from pathlib import Path

import pytest

from next.pages.checks.groups import (
    check_parenthesised_directory_names,
    check_pointless_route_groups,
)
from tests.support import write_page
from tests.support.cases.groupnames import (
    DIRECTORY_NAME_CASES,
    POINTLESS_GROUP_CASES,
    DirectoryNameCase,
    PointlessGroupCase,
)
from tests.support.checks import located, run_check


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


class TestParenthesisedDirectoryNames:
    """next.E169 flags a parenthesised name that is neither a group nor an intercept."""

    @pytest.mark.parametrize("case", DIRECTORY_NAME_CASES, ids=lambda c: c.name)
    def test_a_name_is_flagged_or_silent(
        self, root: Path, case: DirectoryNameCase
    ) -> None:
        write_page(root, "photo")
        write_page(root, case.name)

        messages = run_check(check_parenthesised_directory_names, root)

        expected = [("next.E169", case.name)] if case.flagged else []
        assert located(messages, root) == expected

    @pytest.mark.parametrize(
        "case",
        [case for case in DIRECTORY_NAME_CASES if case.flagged],
        ids=lambda c: c.name,
    )
    def test_the_wording_follows_the_reserved_prefix(
        self, root: Path, case: DirectoryNameCase
    ) -> None:
        write_page(root, case.name)

        [message] = run_check(check_parenthesised_directory_names, root)

        assert ("are reserved for intercepting routes" in message.msg) is case.reserved
        assert ("intercept" in message.hint) is case.reserved
        assert ("route group" in message.hint) is not case.reserved

    def test_a_flagged_directory_below_a_valid_group_is_found(self, root: Path) -> None:
        write_page(root, "(app)/(a b)/x")

        messages = run_check(check_parenthesised_directory_names, root)

        assert located(messages, root) == [("next.E169", "(app)/(a b)")]

    def test_a_tree_without_parentheses_is_silent(self, root: Path) -> None:
        write_page(root, "about/team")

        assert run_check(check_parenthesised_directory_names, root) == []


class TestPointlessRouteGroups:
    """next.W137 warns about a group that wraps one directory and adds nothing."""

    @pytest.mark.parametrize("case", POINTLESS_GROUP_CASES, ids=lambda c: c.id)
    def test_a_group_is_flagged_or_silent(
        self, root: Path, case: PointlessGroupCase
    ) -> None:
        for relative in case.files:
            file = root / relative
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("")

        messages = run_check(check_pointless_route_groups, root)

        expected = [("next.W137", "(g)")] if case.flagged else []
        assert located(messages, root) == expected

    def test_the_message_names_the_lone_child(self, root: Path) -> None:
        write_page(root, "(g)/x")

        [message] = run_check(check_pointless_route_groups, root)

        assert "holds only 'x'" in message.msg
        assert "Move 'x' up a level" in message.hint

    def test_a_directory_that_is_no_group_is_silent(self, root: Path) -> None:
        write_page(root, "about/x")

        assert run_check(check_pointless_route_groups, root) == []
