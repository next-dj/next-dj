import pytest
from e2e_support.browser import (
    PageProbe,
    assert_same_document,
    mark_document,
    wait_for_runtime,
)
from library.models import Book, Chapter
from playwright.sync_api import Locator, Page, expect


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
MODAL = "dialog[data-next-dialog]:modal"
CHANGELIST = "/admin/library/book/"
CHANGELIST_TITLE = "Books · next.dj admin"


def change_path(book: Book) -> str:
    return f"/admin/library/book/{book.pk}/change/"


def open_changelist(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}{CHANGELIST}")
    wait_for_runtime(page)
    mark_document(page)


def open_quick_edit(page: Page, base_url: str, book: Book) -> Locator:
    row = page.locator(f"tr[data-next-key='{book.pk}']")
    row.get_by_role("link", name="Quick edit").click()
    dialog = page.locator(MODAL)
    expect(dialog).to_have_count(1)
    expect(dialog.get_by_role("heading", name="Quick edit")).to_be_visible()
    expect(page).to_have_url(f"{base_url}{change_path(book)}")
    return dialog


def test_quick_edit_opens_over_the_changelist_and_back_closes_it(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)
    expect(page).to_have_title(CHANGELIST_TITLE)

    dialog = open_quick_edit(page, base_url, book)

    expect(page).to_have_title("Quick edit · Dracula · next.dj admin")
    expect(dialog.locator("#id_book_title")).to_have_value("Dracula")
    expect(
        dialog.get_by_role("button", name="Save and continue editing")
    ).to_have_count(0)

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{CHANGELIST}")
    expect(page).to_have_title(CHANGELIST_TITLE)
    assert_same_document(page)


def test_escape_closes_the_quick_editor_and_restores_the_title(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)
    open_quick_edit(page, base_url, book)

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{CHANGELIST}")
    expect(page).to_have_title(CHANGELIST_TITLE)


def test_reloading_the_quick_edit_url_renders_the_change_page(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)
    open_quick_edit(page, base_url, book)

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("heading", name="Edit", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Save and continue editing")).to_be_visible()
    expect(page).to_have_title(CHANGELIST_TITLE)


def test_the_plain_row_link_navigates_to_the_change_page(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)

    with page.expect_navigation():
        page.get_by_role("link", name="Dracula", exact=True).click()

    expect(page).to_have_url(f"{base_url}{change_path(book)}")
    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("heading", name="Edit", exact=True)).to_be_visible()


def test_an_invalid_quick_save_shows_its_error_inside_the_modal(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)
    dialog = open_quick_edit(page, base_url, book)

    dialog.locator("#id_book_title").fill("Dracula, revised")
    dialog.locator("#id_book_published_at").fill("next spring")
    dialog.get_by_role("button", name="Save", exact=True).click()

    expect(dialog.get_by_text("Enter a valid date.")).to_be_visible()
    expect(dialog.locator("#id_book_title")).to_have_value("Dracula, revised")
    expect(page.locator(MODAL)).to_have_count(1)
    expect(page).to_have_url(f"{base_url}{change_path(book)}")
    assert_same_document(page)
    book.refresh_from_db()
    assert book.title == "Dracula"


def test_a_valid_quick_save_closes_the_modal_and_refreshes_the_row(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)
    dialog = open_quick_edit(page, base_url, book)

    dialog.locator("#id_book_title").fill("Dracula, revised")
    dialog.get_by_role("button", name="Save", exact=True).click()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator(f"tr[data-next-key='{book.pk}']")).to_contain_text(
        "Dracula, revised"
    )
    expect(
        page.get_by_text("The book Dracula, revised was updated successfully.")
    ).to_be_visible()
    expect(page).to_have_url(f"{base_url}{CHANGELIST}")
    expect(page).to_have_title(CHANGELIST_TITLE)
    assert_same_document(page)
    book.refresh_from_db()
    assert book.title == "Dracula, revised"


def test_the_history_peek_opens_from_the_change_page(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    page.goto(f"{base_url}{change_path(book)}")
    wait_for_runtime(page)
    mark_document(page)

    page.get_by_role("link", name="History", exact=True).click()

    dialog = page.locator(MODAL)
    expect(dialog.get_by_text("Recent changes")).to_be_visible()
    expect(dialog.get_by_text("No history yet")).to_be_visible()
    expect(page).to_have_url(f"{base_url}/admin/library/book/{book.pk}/history/")
    expect(page).to_have_title("History · Dracula · next.dj admin")

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{change_path(book)}")
    expect(page).to_have_title(CHANGELIST_TITLE)
    assert_same_document(page)


def test_the_history_peek_stacks_over_the_quick_editor(
    page: Page, base_url: str, signed_in: None, make_book
) -> None:
    book = make_book("Dracula")
    open_changelist(page, base_url)
    quick = open_quick_edit(page, base_url, book)

    quick.get_by_role("link", name="History", exact=True).click()

    expect(page.locator(MODAL)).to_have_count(2)
    expect(page.locator(MODAL).last.get_by_text("Recent changes")).to_be_visible()
    expect(page).to_have_title("History · Dracula · next.dj admin")

    page.keyboard.press("Escape")

    expect(page.locator(MODAL)).to_have_count(1)
    expect(page).to_have_title("Quick edit · Dracula · next.dj admin")
    expect(page).to_have_url(f"{base_url}{change_path(book)}")

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_title(CHANGELIST_TITLE)
    expect(page).to_have_url(f"{base_url}{CHANGELIST}")
    assert_same_document(page)


def open_outline(page: Page, base_url: str, book: Book) -> None:
    page.goto(f"{base_url}/admin/library/book/{book.pk}/outline/")
    wait_for_runtime(page)
    mark_document(page)


def test_the_editor_peeks_at_the_manuscript_of_an_own_draft(
    page: Page, base_url: str, editor_signed_in: None, editor, make_book, make_chapter
) -> None:
    draft = make_book("Mathilda", status=Book.DRAFT, editor=editor)
    make_chapter(draft, 1, "Intro", 100)
    make_chapter(draft, 2, "Rising", 250)
    open_outline(page, base_url, draft)

    page.get_by_role("link", name="Peek at the manuscript").click()

    dialog = page.locator(MODAL)
    expect(dialog.locator("[data-peek-words]")).to_have_text("350")
    expect(dialog.get_by_text("2. Rising")).to_be_visible()
    expect(page).to_have_url(f"{base_url}/admin/library/book/{draft.pk}/manuscript/")
    expect(page).to_have_title("Manuscript peek · Mathilda · next.dj admin")

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_title("Mathilda · next.dj admin")
    assert_same_document(page)


def test_a_peek_the_intercept_refuses_falls_back_to_the_full_manuscript(
    page: Page,
    base_url: str,
    editor_signed_in: None,
    make_book,
    make_chapter,
    next_probe: PageProbe,
) -> None:
    unassigned = make_book("The Invisible Man", status=Book.DRAFT)
    make_chapter(unassigned, 1, "Refraction", 400)
    open_outline(page, base_url, unassigned)
    manuscript = f"{base_url}/admin/library/book/{unassigned.pk}/manuscript/"

    with (
        page.expect_console_message(lambda message: "403" in message.text),
        page.expect_response(
            lambda response: response.url == manuscript and response.status == 403
        ) as denial,
        page.expect_navigation(url=manuscript),
    ):
        page.get_by_role("link", name="Peek at the manuscript").click()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("button", name="Append chapter")).to_be_visible()
    expect(page).to_have_title("The Invisible Man · next.dj admin")
    assert denial.value.request.headers["x-next-zone"] == "manuscript-peek"
    assert next_probe.bad_responses == [f"403 {manuscript}"]
    assert [entry for entry in next_probe.console if "403" not in entry] == []
    next_probe.bad_responses.clear()
    next_probe.console.clear()
    assert Chapter.objects.filter(book=unassigned).count() == 1


def test_a_peek_after_the_session_ended_lands_on_the_login(
    page: Page, base_url: str, signed_in: None, make_book, next_probe: PageProbe
) -> None:
    draft = make_book("Mathilda", status=Book.DRAFT)
    open_outline(page, base_url, draft)
    page.context.clear_cookies()
    login = f"{base_url}/admin/login/?next=/admin/library/book/{draft.pk}/manuscript/"

    with (
        page.expect_response(
            lambda response: response.url == login and response.status == 400
        ) as followed,
        page.expect_navigation(url=login),
    ):
        page.get_by_role("link", name="Peek at the manuscript").click()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("button", name="Sign in")).to_be_visible()
    assert followed.value.request.headers["x-next-zone"] == "manuscript-peek"
    assert next_probe.bad_responses == [f"400 {login}"]
    assert [entry for entry in next_probe.console if "400" not in entry] == []
    next_probe.bad_responses.clear()
    next_probe.console.clear()
