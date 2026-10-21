import pytest
from admin_audit.models import AdminActivityLog
from e2e_support.browser import (
    applied_count,
    assert_same_document,
    mark_document,
    wait_for_apply,
    wait_for_runtime,
)
from library.demo import EDITOR_PASSWORD, EDITOR_USERNAME
from library.models import Book, Tag
from playwright.sync_api import Page, Response, expect

from next.testing import NextClient


pytestmark = pytest.mark.e2e

DASHBOARD_PANELS = "dashboard-panels"
ZONE_HEADERS = {"X-Next-Request": "1", "X-Next-Zone": DASHBOARD_PANELS}
MANUSCRIPT_HEADERS = {"X-Next-Request": "1", "X-Next-Zone": "manuscript"}


def is_activity_poll(response: Response) -> bool:
    return DASHBOARD_PANELS in response.request.headers.get("x-next-zone", "")


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


def test_an_anonymous_visit_to_the_history_lands_on_the_login_form(
    page: Page, base_url: str, book: Book
) -> None:
    history = f"/admin/library/book/{book.pk}/history/"
    page.goto(f"{base_url}{history}")

    expect(page).to_have_url(f"{base_url}/admin/login/?next={history}")
    expect(page.get_by_role("button", name="Sign in")).to_be_visible()


def test_an_anonymous_action_post_is_sent_to_the_login(
    page: Page, base_url: str, make_tag
) -> None:
    tag = make_tag("Gothic")
    page.goto(f"{base_url}/admin/login/")
    token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()

    response = page.request.post(
        f"{base_url}{NextClient().get_action_url('admin:delete')}",
        form={
            "csrfmiddlewaretoken": token,
            "_next_form_origin": f"/admin/library/tag/{tag.pk}/delete/",
        },
        max_redirects=0,
    )

    assert response.status == 302
    assert response.headers["location"].startswith("/admin/login/")
    assert Tag.objects.filter(pk=tag.pk).exists()


def test_an_anonymous_zone_request_is_sent_to_the_login(
    page: Page, base_url: str
) -> None:
    response = page.request.get(
        f"{base_url}/admin/", headers=ZONE_HEADERS, max_redirects=0
    )

    assert response.status == 302
    assert response.headers["location"] == "/admin/login/?next=/admin/"


def test_the_editor_gets_no_activity_panel_and_no_rows_from_the_zone(
    page: Page, base_url: str, editor
) -> None:
    AdminActivityLog.objects.create(
        action="change", object_repr="Frankenstein", response_status=302
    )
    sign_in_as_editor(page, base_url, "/admin/")

    expect(page.get_by_role("heading", name="Recent activity")).to_have_count(0)
    expect(page.get_by_role("link", name="Activity feed")).to_have_count(0)
    expect(page.get_by_text("Frankenstein")).to_have_count(0)
    response = page.request.get(f"{base_url}/admin/", headers=ZONE_HEADERS)
    assert response.status == 200
    assert "Frankenstein" not in response.text()


@pytest.mark.usefixtures("signed_in")
def test_the_superuser_watches_the_activity_zone_refresh(
    page: Page, base_url: str
) -> None:
    page.clock.install()
    page.goto(f"{base_url}/admin/")
    wait_for_runtime(page)
    feed = page.locator(f"[data-next-zone='{DASHBOARD_PANELS}']")
    expect(feed).to_contain_text("No activity yet.")

    AdminActivityLog.objects.create(
        action="change", object_repr="Carmilla", response_status=302
    )
    with page.expect_response(is_activity_poll):
        page.clock.run_for(5_000)

    expect(feed).to_contain_text("Carmilla")
    expect(feed.get_by_text("No activity yet.")).to_have_count(0)


@pytest.mark.usefixtures("signed_in")
def test_the_history_crumbs_climb_to_the_changelist(
    page: Page, base_url: str, book: Book
) -> None:
    page.goto(f"{base_url}/admin/library/book/{book.pk}/history/")
    wait_for_runtime(page)

    trail = page.get_by_role("navigation", name="Breadcrumb").get_by_role("link")
    expect(trail).to_have_text(["Dashboard", "Books", "History"])
    expect(trail.nth(1)).to_have_attribute("href", "/admin/library/book/")
    trail.nth(1).click()

    expect(page).to_have_url(f"{base_url}/admin/library/book/")


def assert_manuscript_refused(page: Page, base_url: str, book: Book) -> None:
    path = f"/admin/library/book/{book.pk}/manuscript/"
    assert page.request.get(f"{base_url}{path}").status == 403
    zone = page.request.get(f"{base_url}{path}", headers=MANUSCRIPT_HEADERS)
    assert zone.status == 403
    assert "Secret" not in zone.text()
    token = page.locator("input[name='csrfmiddlewaretoken']").first.input_value()
    response = page.request.post(
        f"{base_url}{NextClient().get_action_url('add_chapter_form')}",
        form={
            "csrfmiddlewaretoken": token,
            "_next_form_origin": path,
            "title": "Coda",
            "word_count": "300",
        },
        max_redirects=0,
    )
    assert response.status == 403
    assert list(book.chapters.values_list("title", flat=True)) == ["Secret"]


def test_the_editor_reads_the_outline_of_a_published_book_only(
    page: Page, base_url: str, editor, make_book, make_chapter
) -> None:
    published = make_book("Dracula", status=Book.PUBLISHED, editor=editor)
    make_chapter(published, title="Secret")
    sign_in_as_editor(page, base_url, f"/admin/library/book/{published.pk}/change/")
    page.get_by_role("link", name="Outline").click()

    expect(page).to_have_url(f"{base_url}/admin/library/book/{published.pk}/outline/")
    wait_for_runtime(page)
    outline = page.locator("[data-next-zone='outline']")
    expect(outline.get_by_role("cell", name="Secret")).to_be_visible()
    expect(outline.locator("[data-outline-read-only]")).to_be_visible()
    expect(page.get_by_role("link", name="Open the manuscript")).to_have_count(0)
    assert_manuscript_refused(page, base_url, published)


def test_the_editor_is_refused_the_manuscript_of_a_foreign_draft(
    page: Page, base_url: str, editor, copyeditor, make_book, make_chapter
) -> None:
    foreign = make_book("Emma", status=Book.DRAFT, editor=copyeditor)
    make_chapter(foreign, title="Secret")
    sign_in_as_editor(page, base_url, f"/admin/library/book/{foreign.pk}/outline/")

    expect(page.locator("[data-outline-read-only]")).to_be_visible()
    expect(page.get_by_role("link", name="Open the manuscript")).to_have_count(0)
    assert_manuscript_refused(page, base_url, foreign)


def test_the_editor_appends_a_chapter_to_an_own_draft(
    page: Page, base_url: str, editor, make_book, make_chapter
) -> None:
    draft = make_book("Mathilda", status=Book.DRAFT, editor=editor)
    make_chapter(draft, 1, "Intro", 100)
    sign_in_as_editor(page, base_url, f"/admin/library/book/{draft.pk}/outline/")
    page.get_by_role("link", name="Open the manuscript").click()

    expect(page).to_have_url(f"{base_url}/admin/library/book/{draft.pk}/manuscript/")
    wait_for_runtime(page)
    trail = page.get_by_role("navigation", name="Breadcrumb").get_by_role("link")
    expect(trail).to_have_text(["Dashboard", "Manuscript"])
    manuscript = page.locator("[data-next-zone='manuscript']")
    expect(manuscript.get_by_role("row")).to_have_count(3)
    mark_document(page)
    seen = applied_count(page)
    page.get_by_label("Chapter title").fill("Rising")
    page.get_by_label("Words").fill("250")
    page.get_by_role("button", name="Append chapter").click()

    wait_for_apply(page, seen)
    expect(manuscript.get_by_role("row", name="2 Rising 250")).to_be_visible()
    expect(manuscript.locator("[data-total-words]")).to_have_text("350")
    expect(page.get_by_label("Chapter title")).to_have_value("")
    assert_same_document(page)
    assert draft.chapters.get(number=2).title == "Rising"


@pytest.mark.usefixtures("signed_in")
def test_the_superuser_appends_a_chapter_to_a_published_book(
    page: Page, base_url: str, make_book
) -> None:
    published = make_book("Dracula", status=Book.PUBLISHED)
    page.goto(f"{base_url}/admin/library/book/{published.pk}/change/")
    page.get_by_role("link", name="Outline").click()
    page.get_by_role("link", name="Open the manuscript").click()

    expect(page).to_have_url(
        f"{base_url}/admin/library/book/{published.pk}/manuscript/"
    )
    wait_for_runtime(page)
    manuscript = page.locator("[data-next-zone='manuscript']")
    expect(manuscript.get_by_text("No chapters yet.")).to_be_visible()
    mark_document(page)
    seen = applied_count(page)
    page.get_by_label("Chapter title").fill("Epilogue")
    page.get_by_label("Words").fill("120")
    page.get_by_role("button", name="Append chapter").click()

    wait_for_apply(page, seen)
    expect(manuscript.get_by_role("row", name="1 Epilogue 120")).to_be_visible()
    assert_same_document(page)
    assert published.chapters.get().title == "Epilogue"
