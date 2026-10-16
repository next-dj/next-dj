import pytest
from e2e_support.browser import wait_for_runtime
from library.demo import EDITOR_PASSWORD, EDITOR_USERNAME
from library.models import Book
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e


def sign_in_as_editor(page: Page, base_url: str, next_path: str) -> None:
    page.goto(f"{base_url}/admin/login/?next={next_path}")
    page.get_by_label("Username").fill(EDITOR_USERNAME)
    page.get_by_label("Password").fill(EDITOR_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    expect(page).to_have_url(f"{base_url}{next_path}")
    wait_for_runtime(page)


def test_the_editor_reads_a_published_book_without_save_or_delete(
    page: Page, base_url: str, editor, make_book, make_chapter
) -> None:
    published = make_book("Dracula", status=Book.PUBLISHED)
    chapter = make_chapter(published)
    sign_in_as_editor(page, base_url, f"/admin/library/book/{published.pk}/change/")

    expect(page.locator("[data-read-only]")).to_be_visible()
    expect(page.get_by_role("button", name="Save", exact=True)).to_have_count(0)
    expect(page.get_by_role("link", name="Delete")).to_have_count(0)
    expect(page.locator(f"[data-inline-read-only='{chapter.pk}']")).to_contain_text(
        chapter.title
    )
    expect(page.locator(f"form[data-next-key='{chapter.pk}']")).to_have_count(0)
    expect(page.get_by_role("button", name="Add chapter")).to_have_count(0)


def test_the_editor_saves_a_draft_book(
    page: Page, base_url: str, editor, make_book, make_chapter
) -> None:
    draft = make_book("Mathilda", status=Book.DRAFT)
    chapter = make_chapter(draft)
    sign_in_as_editor(page, base_url, f"/admin/library/book/{draft.pk}/change/")

    expect(page.get_by_role("link", name="Delete")).to_be_visible()
    expect(page.locator(f"form[data-next-key='{chapter.pk}']")).to_be_visible()
    expect(page.get_by_role("button", name="Add chapter")).to_be_visible()
    main_form = page.locator("form", has=page.locator("#id_book_title"))
    main_form.locator("#id_book_title").fill("Mathilda, revised")
    main_form.get_by_role("button", name="Save", exact=True).click()

    expect(page).to_have_url(f"{base_url}/admin/library/book/")
    draft.refresh_from_db()
    assert draft.title == "Mathilda, revised"


@pytest.mark.usefixtures("signed_in")
def test_the_superuser_keeps_save_on_a_published_book(
    page: Page, base_url: str, make_book
) -> None:
    published = make_book("Dracula", status=Book.PUBLISHED)
    page.goto(f"{base_url}/admin/library/book/{published.pk}/change/")
    wait_for_runtime(page)

    expect(page.get_by_role("button", name="Save", exact=True)).to_be_visible()
    expect(page.locator("[data-read-only]")).to_have_count(0)
