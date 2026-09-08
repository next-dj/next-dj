import re
from collections.abc import Callable

import pytest
from django.contrib.auth.models import User
from e2e_support.browser import (
    PageProbe,
    applied_count,
    wait_for_apply,
    wait_for_runtime,
)
from library.models import Book, Chapter
from playwright.sync_api import Locator, Page, expect


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
BULK_TOGGLE = "thead input[data-bulk-toggle]"
ROW_BOXES = "tbody input[name='_selected_action']"
CHECKED_BOXES = "tbody input[name='_selected_action']:checked"
STATUS_CELLS = "tbody tr td:nth-child(4)"
COUNT_BADGE = "[data-inline-count='chapter']"
REQUIRED_ERROR = "This field is required."
DUPLICATE_ERROR = "Chapter with this Book and Number already exists."
LOGIN_URL = re.compile(r"/admin/login/")
COUNT_IDS = (
    "() => { const ids = [...document.querySelectorAll('[id]')].map(n => n.id);"
    " return ids.length - new Set(ids).size; }"
)

TAG_ROW = (
    "(pk) => { document.querySelector"
    "(`form[data-next-key='${pk}']`).survivedTheSave = true; }"
)
READ_ROW_TAG = (
    "(pk) => document.querySelector(`form[data-next-key='${pk}']`).survivedTheSave"
)


def inline_row(page: Page, pk: int) -> Locator:
    return page.locator(f"form[data-next-key='{pk}']")


def add_row_form(page: Page) -> Locator:
    return page.locator("form[data-next-key='chapter']")


def open_change_page(page: Page, base_url: str, book: Book) -> None:
    page.goto(f"{base_url}/admin/library/book/{book.pk}/change/")
    wait_for_runtime(page)


def submit_new_chapter(page: Page) -> Locator:
    add_form = add_row_form(page)
    add_form.locator("#id_chapter_add_number").fill("3")
    add_form.locator("#id_chapter_add_title").fill("Climax")
    add_form.locator("#id_chapter_add_word_count").fill("300")
    add_form.get_by_role("button", name="Add chapter").click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    expect(dialog.locator("#id_chapter_title")).to_have_value("Climax")
    return dialog


def test_runtime_boots_and_serves_its_bundle(
    page: Page, base_url: str, signed_in: None, next_probe: PageProbe
) -> None:
    page.goto(f"{base_url}/admin/")
    wait_for_runtime(page)

    bundle = [
        response
        for response in next_probe.responses
        if response.url.endswith("/static/next/next.min.js")
    ]
    assert [response.status for response in bundle] == [200]
    assert page.evaluate("() => typeof window.Next") == "function"
    expect(page.get_by_role("heading", name="Library")).to_be_visible()
    assert next_probe.partial_requests() == []


def test_an_anonymous_visit_lands_on_the_login_form_and_signing_in_returns(
    page: Page, base_url: str, admin_user: User
) -> None:
    page.goto(f"{base_url}/admin/")

    expect(page).to_have_url(f"{base_url}/admin/login/?next=/admin/")
    wait_for_runtime(page)
    expect(page.locator("#id_username")).to_be_focused()

    page.fill("#id_username", "admin")
    page.fill("#id_password", "admin-pass")
    page.get_by_role("button", name="Sign in").click()

    expect(page).to_have_url(f"{base_url}/admin/")
    expect(page.get_by_text("Welcome, admin.")).to_be_visible()
    expect(page.get_by_role("button", name="Sign out")).to_be_visible()


def test_signing_out_clears_the_session_and_relocks_the_dashboard(
    page: Page, base_url: str, signed_in: None
) -> None:
    page.goto(f"{base_url}/admin/")
    wait_for_runtime(page)

    page.get_by_role("button", name="Sign out").click()

    expect(page).to_have_url(f"{base_url}/admin/logout/")
    expect(page.get_by_text("You have been signed out.")).to_be_visible()

    page.goto(f"{base_url}/admin/")

    expect(page).to_have_url(LOGIN_URL)
    expect(page.locator("#id_password")).to_be_visible()


def test_the_header_checkbox_drives_every_row_checkbox(
    page: Page, base_url: str, signed_in: None, demo_data: None, next_probe: PageProbe
) -> None:
    page.goto(f"{base_url}/admin/library/book/")
    wait_for_runtime(page)

    expect(page.locator(ROW_BOXES)).to_have_count(12)
    expect(page.locator(CHECKED_BOXES)).to_have_count(0)

    page.locator(BULK_TOGGLE).check()

    expect(page.locator(CHECKED_BOXES)).to_have_count(12)

    page.locator(BULK_TOGGLE).uncheck()

    expect(page.locator(CHECKED_BOXES)).to_have_count(0)
    assert next_probe.partial_requests() == []


def test_applying_a_bulk_action_publishes_the_whole_selection(
    page: Page, base_url: str, signed_in: None, make_book: Callable[..., Book]
) -> None:
    for title in ("Alpha", "Beta", "Gamma"):
        make_book(title, status=Book.DRAFT)
    page.goto(f"{base_url}/admin/library/book/")
    wait_for_runtime(page)
    expect(page.locator(STATUS_CELLS)).to_have_text(["Draft"] * 3)

    page.locator(BULK_TOGGLE).check()
    expect(page.locator(CHECKED_BOXES)).to_have_count(3)
    page.select_option("#bulk-action-select", "mark_as_published")
    page.get_by_role("button", name="Apply").click()

    expect(page.get_by_text("3 book(s) marked as published.")).to_be_visible()
    expect(page.locator(STATUS_CELLS)).to_have_text(["Published"] * 3)
    assert Book.objects.filter(status=Book.DRAFT).count() == 0


def test_a_bulk_action_returns_to_the_filtered_changelist(
    page: Page, base_url: str, signed_in: None, make_book: Callable[..., Book]
) -> None:
    for title in ("Alpha", "Beta"):
        make_book(title, status=Book.DRAFT)
    make_book("Delta", status=Book.PUBLISHED)
    filtered = f"{base_url}/admin/library/book/?status__exact=draft"
    page.goto(filtered)
    wait_for_runtime(page)
    expect(page.locator(STATUS_CELLS)).to_have_text(["Draft"] * 2)

    page.locator(BULK_TOGGLE).check()
    page.select_option("#bulk-action-select", "mark_as_published")
    page.get_by_role("button", name="Apply").click()

    expect(page.locator(STATUS_CELLS)).to_have_count(0)
    expect(page).to_have_url(filtered)
    assert Book.objects.filter(status=Book.DRAFT).count() == 0


def test_saving_one_inline_row_leaves_its_neighbours_untouched(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
    next_probe: PageProbe,
) -> None:
    book, first, second = book_with_two_chapters
    open_change_page(page, base_url, book)
    expect(page.locator(COUNT_BADGE)).to_have_text("2 saved")

    page.evaluate(TAG_ROW, first.pk)
    inline_row(page, first.pk).locator(f"#id_chapter_{first.pk}_title").fill("Typed")
    page.fill("#id_book_title", "Retitled but unsaved")

    seen = applied_count(page)
    inline_row(page, second.pk).locator(f"#id_chapter_{second.pk}_title").fill(
        "Rising action"
    )
    inline_row(page, second.pk).get_by_role("button", name="Save").click()
    wait_for_apply(page, seen)

    expect(
        inline_row(page, second.pk).locator(f"#id_chapter_{second.pk}_title")
    ).to_have_value("Rising action")
    assert page.evaluate(READ_ROW_TAG, first.pk) is True
    expect(
        inline_row(page, first.pk).locator(f"#id_chapter_{first.pk}_title")
    ).to_have_value("Typed")
    expect(page.locator("#id_book_title")).to_have_value("Retitled but unsaved")
    expect(page.locator(COUNT_BADGE)).to_have_text("2 saved")

    second.refresh_from_db()
    first.refresh_from_db()
    book.refresh_from_db()
    assert second.title == "Rising action"
    assert first.title == "Intro"
    assert book.title == "Book"

    posts = [
        response
        for response in next_probe.partial_requests()
        if response.request.method == "POST"
    ]
    assert [response.status for response in posts] == [200]


def test_an_empty_inline_title_never_reaches_the_server(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
    next_probe: PageProbe,
) -> None:
    book, _first, second = book_with_two_chapters
    open_change_page(page, base_url, book)

    title = inline_row(page, second.pk).locator(f"#id_chapter_{second.pk}_title")
    title.fill("")
    inline_row(page, second.pk).get_by_role("button", name="Save").click()

    assert title.evaluate("element => element.validity.valueMissing") is True
    assert next_probe.partial_requests() == []
    second.refresh_from_db()
    assert second.title == "Rising"


def test_a_server_rejected_row_re_renders_alone_with_its_error(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
) -> None:
    book, first, second = book_with_two_chapters
    open_change_page(page, base_url, book)

    page.evaluate(TAG_ROW, first.pk)
    page.fill("#id_book_title", "Retitled but unsaved")
    inline_row(page, first.pk).locator(f"#id_chapter_{first.pk}_title").fill("Typed")

    row = inline_row(page, second.pk)
    row.evaluate("element => { element.noValidate = true; }")
    row.locator(f"#id_chapter_{second.pk}_title").fill("")
    row.get_by_role("button", name="Save").click()

    expect(inline_row(page, second.pk).get_by_text(REQUIRED_ERROR)).to_be_visible()
    expect(inline_row(page, first.pk).get_by_text(REQUIRED_ERROR)).to_have_count(0)
    assert page.evaluate(READ_ROW_TAG, first.pk) is True
    expect(
        inline_row(page, first.pk).locator(f"#id_chapter_{first.pk}_title")
    ).to_have_value("Typed")
    expect(page.locator("#id_book_title")).to_have_value("Retitled but unsaved")
    expect(page.locator(COUNT_BADGE)).to_have_text("2 saved")

    second.refresh_from_db()
    assert second.title == "Rising"


def test_adding_a_chapter_opens_its_editor_in_a_real_modal(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
) -> None:
    book, first, _second = book_with_two_chapters
    open_change_page(page, base_url, book)
    page.evaluate(TAG_ROW, first.pk)
    expect(page.locator(DIALOG)).to_have_count(0)

    dialog = submit_new_chapter(page)

    expect(dialog.get_by_role("button", name="Discard")).to_be_visible()
    assert dialog.evaluate("element => element.open") is True
    assert dialog.evaluate("element => element.matches(':modal')") is True

    chapter = Chapter.objects.get(book=book, number=3)
    assert chapter.title == "Climax"
    expect(dialog.locator("#id_chapter_title")).to_have_value("Climax")
    expect(page.locator(COUNT_BADGE)).to_have_text("3 saved")
    assert page.evaluate(READ_ROW_TAG, first.pk) is True


def test_the_layered_editor_keeps_its_own_field_ids(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
) -> None:
    book, *_ = book_with_two_chapters
    open_change_page(page, base_url, book)
    page.fill("#id_book_title", "Retitled but unsaved")

    dialog = submit_new_chapter(page)

    assert page.evaluate(COUNT_IDS) == 0
    expect(page.locator("#id_chapter_title")).to_have_count(1)
    dialog.locator("label[for='id_chapter_title']").click()

    expect(dialog.locator("#id_chapter_title")).to_be_focused()
    expect(page.locator("#id_book_title")).to_have_value("Retitled but unsaved")


def test_a_duplicate_chapter_number_answers_with_a_row_error(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
    next_probe: PageProbe,
) -> None:
    book, first, _second = book_with_two_chapters
    open_change_page(page, base_url, book)

    add_form = add_row_form(page)
    add_form.locator("#id_chapter_add_number").fill(str(first.number))
    add_form.locator("#id_chapter_add_title").fill("Dupe")
    add_form.locator("#id_chapter_add_word_count").fill("10")
    add_form.get_by_role("button", name="Add chapter").click()

    expect(add_row_form(page).get_by_text(DUPLICATE_ERROR)).to_be_visible()
    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator(COUNT_BADGE)).to_have_text("2 saved")
    posts = [
        response
        for response in next_probe.partial_requests()
        if response.request.method == "POST"
    ]
    assert [response.status for response in posts] == [200]
    assert Chapter.objects.filter(book=book).count() == 2


def test_discard_dismisses_the_editor_layer(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
) -> None:
    book, *_ = book_with_two_chapters
    open_change_page(page, base_url, book)
    expect(page.get_by_role("button", name="Discard")).to_be_hidden()

    dialog = submit_new_chapter(page)

    dialog.get_by_role("button", name="Discard").click()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator(COUNT_BADGE)).to_have_text("3 saved")
    expect(page).to_have_url(f"{base_url}/admin/library/book/{book.pk}/change/")


def test_a_click_outside_the_editor_layer_dismisses_it(
    page: Page,
    base_url: str,
    signed_in: None,
    book_with_two_chapters: tuple[Book, Chapter, Chapter],
) -> None:
    book, first, _second = book_with_two_chapters
    open_change_page(page, base_url, book)
    page.evaluate(TAG_ROW, first.pk)

    dialog = submit_new_chapter(page)
    dialog.click(position={"x": 2, "y": 2})

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator(COUNT_BADGE)).to_have_text("3 saved")
    assert page.evaluate(READ_ROW_TAG, first.pk) is True
    assert Chapter.objects.filter(book=book, number=3).count() == 1
