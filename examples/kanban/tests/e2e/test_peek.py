import pytest
from e2e_support.browser import (
    applied_count,
    assert_same_document,
    mark_document,
    wait_for_apply,
    wait_for_runtime,
)
from kanban.models import Board
from playwright.sync_api import Locator, Page, expect


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
MODAL = "dialog[data-next-dialog]:modal"
INDEX_TITLE = "next.dj — Kanban"


@pytest.fixture()
def board(demo_data: None) -> Board:
    return Board.objects.get(slug="engineering-roadmap")


def open_index(page: Page, base_url: str) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    mark_document(page)


def open_peek(page: Page, base_url: str, board: Board) -> Locator:
    seen = applied_count(page)
    card = page.locator("li").filter(has_text=board.title)
    card.get_by_role("link", name="Peek at columns").click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    wait_for_apply(page, seen)
    expect(dialog.locator("[data-board-peek]")).to_be_visible()
    expect(page.locator(MODAL)).to_have_count(1)
    expect(page).to_have_url(f"{base_url}/board/{board.pk}/")
    return dialog


def test_the_peek_opens_the_columns_over_the_index_and_back_closes_it(
    page: Page, base_url: str, board: Board
) -> None:
    open_index(page, base_url)
    expect(page).to_have_title(INDEX_TITLE)

    dialog = open_peek(page, base_url, board)

    expect(page).to_have_title("Peek · Engineering roadmap · next.dj Kanban")
    expect(dialog.locator("[data-peek-lane]")).to_have_count(4)
    expect(dialog).to_contain_text("Backlog")

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/")
    expect(page).to_have_title(INDEX_TITLE)
    assert_same_document(page)


def test_escape_closes_the_peek_and_restores_the_title(
    page: Page, base_url: str, board: Board
) -> None:
    open_index(page, base_url)
    open_peek(page, base_url, board)

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/")
    expect(page).to_have_title(INDEX_TITLE)


def test_the_peek_links_to_the_real_board(
    page: Page, base_url: str, board: Board
) -> None:
    open_index(page, base_url)
    dialog = open_peek(page, base_url, board)

    dialog.get_by_role("link", name="Open the board").click()

    expect(page.locator("#kanban-board")).to_be_visible()
    expect(page.locator(DIALOG)).to_have_count(0)


def test_the_board_title_link_still_navigates_without_a_modal(
    page: Page, base_url: str, board: Board
) -> None:
    open_index(page, base_url)

    page.get_by_role("link", name="Engineering roadmap").click()

    expect(page).to_have_url(f"{base_url}/board/{board.pk}/")
    expect(page.locator(DIALOG)).to_have_count(0)
