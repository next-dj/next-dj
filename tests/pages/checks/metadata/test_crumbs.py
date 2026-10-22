from pathlib import Path

import pytest

from next.pages.checks.metadata.crumbs import check_route_group_breadcrumbs
from tests.pages.checks.metadata.trees import templated_page
from tests.support.cases.groupnames import CRUMB_CASES, CrumbCase
from tests.support.checks import located, run_check


class TestRouteGroupBreadcrumbs:
    """next.W136 warns about a breadcrumb a route group page.py declares in vain."""

    @pytest.mark.parametrize("case", CRUMB_CASES, ids=lambda c: c.id)
    def test_a_page_is_flagged_or_silent(self, tmp_path: Path, case: CrumbCase) -> None:
        root = tmp_path / case.tree
        page_file = templated_page(root / case.directory, case.source)

        messages = run_check(check_route_group_breadcrumbs, root)

        expected = [("next.W136", str(page_file.relative_to(root)))]
        assert located(messages, root) == (expected if case.flagged else [])

    def test_the_message_names_the_group_and_the_fix(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        templated_page(root / "(g)", 'metadata = {"breadcrumb": "Home"}\n')

        [message] = run_check(check_route_group_breadcrumbs, root)

        assert "'(g)' is a route group" in message.msg
        assert "page.py of the parent directory" in message.hint
