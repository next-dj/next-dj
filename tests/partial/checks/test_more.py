from pathlib import Path

import pytest

from next.partial.checks import (
    E_MORE_WITHOUT_WINDOW,
    E_MORE_ZONE_UNKNOWN,
    E_PAGER_PARAM_SHARED,
    check_more_has_page_window,
    check_more_zone_exists,
    check_pager_params_unique,
)
from tests.support import write_page
from tests.support.access import PAGE_HEAD
from tests.support.checks import located, run_check


ROWS_ZONE = '{% zone "rows" tag="ul" %}{% endzone %}'


def _more(options: str = "") -> str:
    return f'{ROWS_ZONE}{{% more zone="rows" next=n{options} %}}more{{% endmore %}}'


WINDOW_HEAD = (
    f"{PAGE_HEAD}from typing import Annotated\n\n"
    "from next.partial import KeysetPager, NumberPager, Paging\n\n"
    "LEDGER = NumberPager()\n"
    'OTHER = KeysetPager(("pk",), param="page")\n'
    'SPARE = KeysetPager(("pk",))\n\n'
)


def _rows(
    *,
    inherit: bool = False,
    annotation: str = "Annotated[Paging, LEDGER]",
    name: str = "rows",
) -> str:
    flag = ", inherit_context=True" if inherit else ""
    return (
        f'@page.context("{name}"{flag})\n'
        f"def {name}(page: {annotation}) -> object:\n"
        "    return page.paginate([])\n"
    )


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    directory = tmp_path / "pages"
    directory.mkdir()
    return directory


class TestMoreZoneExists:
    """next.E220 flags a `{% more %}` whose zone the page does not declare."""

    def test_an_undeclared_zone_is_flagged(self, root: Path) -> None:
        write_page(
            root, "feed", PAGE_HEAD, body='{% more zone="rows" next=n %}x{% endmore %}'
        )

        [message] = run_check(check_more_zone_exists, root)

        assert located([message], root) == [(E_MORE_ZONE_UNKNOWN, "feed/page.py")]
        assert 'grows the zone "rows"' in message.msg
        assert "zone=" in message.hint

    def test_an_undeclared_zone_above_the_list_is_flagged(self, root: Path) -> None:
        write_page(
            root,
            "feed",
            PAGE_HEAD,
            body='{% more zone="rows" previous=p %}x{% endmore %}',
        )

        [message] = run_check(check_more_zone_exists, root)

        assert message.id == E_MORE_ZONE_UNKNOWN

    def test_a_declared_zone_is_silent(self, root: Path) -> None:
        write_page(root, "feed", PAGE_HEAD, body=_more())

        assert run_check(check_more_zone_exists, root) == []

    def test_a_zone_of_the_layout_counts(self, root: Path) -> None:
        (root / "layout.djx").write_text(f"{ROWS_ZONE}{{% template %}}")
        write_page(
            root, "feed", PAGE_HEAD, body='{% more zone="rows" next=n %}x{% endmore %}'
        )

        assert run_check(check_more_zone_exists, root) == []


class TestMoreHasPageWindow:
    """next.E235 flags `prune=` or `url=` on a page no `Paging` callable serves."""

    @pytest.mark.parametrize("options", [" prune=200", ' url="replace"'])
    def test_a_windowed_tag_without_a_window_is_flagged(
        self, root: Path, options: str
    ) -> None:
        write_page(root, "feed", PAGE_HEAD, body=_more(options))

        [message] = run_check(check_more_has_page_window, root)

        assert located([message], root) == [(E_MORE_WITHOUT_WINDOW, "feed/page.py")]
        assert "prune= or url=" in message.msg
        assert "Annotated[Paging, pager]" in message.hint

    def test_a_callable_taking_the_window_is_silent(self, root: Path) -> None:
        write_page(root, "feed", WINDOW_HEAD + _rows(), body=_more(" prune=200"))

        assert run_check(check_more_has_page_window, root) == []

    def test_an_inherited_window_counts(self, root: Path) -> None:
        write_page(root, "", WINDOW_HEAD + _rows(inherit=True), body="<p>home</p>")
        write_page(root, "feed", PAGE_HEAD, body=_more(' url="push"'))

        assert run_check(check_more_has_page_window, root) == []

    def test_a_window_of_another_page_does_not_count(self, root: Path) -> None:
        write_page(root, "other", WINDOW_HEAD + _rows(), body="<p>other</p>")
        write_page(root, "", WINDOW_HEAD + _rows(), body="<p>home</p>")
        write_page(root, "feed", PAGE_HEAD, body=_more(" prune=50"))

        messages = run_check(check_more_has_page_window, root)

        assert located(messages, root) == [(E_MORE_WITHOUT_WINDOW, "feed/page.py")]

    @pytest.mark.parametrize(
        "annotation", ["int", "Paging", "Annotated[Paging, 'doc']"]
    )
    def test_a_callable_without_a_pager_does_not_count(
        self, root: Path, annotation: str
    ) -> None:
        write_page(
            root,
            "feed",
            WINDOW_HEAD + _rows(annotation=annotation),
            body=_more(" prune=5"),
        )

        [message] = run_check(check_more_has_page_window, root)

        assert message.id == E_MORE_WITHOUT_WINDOW

    def test_a_plain_tag_needs_no_window(self, root: Path) -> None:
        write_page(root, "feed", PAGE_HEAD, body=_more())

        assert run_check(check_more_has_page_window, root) == []


class TestPagerParamsUnique:
    """next.E244 flags two pagers of one page reading the same query parameter."""

    def test_two_pagers_sharing_a_param_are_flagged(self, root: Path) -> None:
        source = (
            WINDOW_HEAD
            + _rows()
            + _rows(name="others", annotation="Annotated[Paging, OTHER]")
        )
        write_page(root, "feed", source, body=_more())

        [message] = run_check(check_pager_params_unique, root)

        assert located([message], root) == [(E_PAGER_PARAM_SHARED, "feed/page.py")]
        assert "2 pagers of" in message.msg
        assert '"page"' in message.msg
        assert "param=" in message.hint

    def test_an_inherited_pager_counts(self, root: Path) -> None:
        write_page(root, "", WINDOW_HEAD + _rows(inherit=True), body="<p>home</p>")
        write_page(
            root,
            "feed",
            WINDOW_HEAD + _rows(name="others", annotation="Annotated[Paging, OTHER]"),
            body=_more(),
        )

        messages = run_check(check_pager_params_unique, root)

        assert located(messages, root) == [(E_PAGER_PARAM_SHARED, "feed/page.py")]

    def test_one_pager_used_twice_is_silent(self, root: Path) -> None:
        write_page(
            root, "feed", WINDOW_HEAD + _rows() + _rows(name="others"), body=_more()
        )

        assert run_check(check_pager_params_unique, root) == []

    def test_pagers_on_their_own_params_are_silent(self, root: Path) -> None:
        source = (
            WINDOW_HEAD
            + _rows()
            + _rows(name="others", annotation="Annotated[Paging, SPARE]")
        )
        write_page(root, "feed", source, body=_more())

        assert run_check(check_pager_params_unique, root) == []
