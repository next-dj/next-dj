import pytest
from e2e_support.browser import PageProbe, wait_for_runtime
from notes.models import Note
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e

NOTE_CARD = "[data-note-card]"
PREVIEW = "[data-markdown-preview] .markdown-body"
ACME_ACCENT = "rgb(37, 99, 235)"
GLOBEX_ACCENT = "rgb(22, 163, 74)"


@pytest.fixture()
def tenant_query_fallback(settings) -> None:
    settings.DEBUG = True


@pytest.fixture()
def acme_note(demo_data) -> Note:
    return Note.objects.get(title="Welcome to Acme")


@pytest.fixture()
def locked_note(demo_data) -> Note:
    return Note.objects.get(title="Status update")


def open_as(page: Page, base_url: str, slug: str, path: str) -> None:
    page.goto(f"{base_url}{path}?tenant={slug}")
    wait_for_runtime(page)


def same_origin_static(probe: PageProbe, base_url: str) -> list[str]:
    return [
        response.url
        for response in probe.responses
        if response.url.startswith(f"{base_url}/") and "/static/" in response.url
    ]


def test_runtime_boots_and_serves_its_bundle(
    page: Page,
    base_url: str,
    demo_data: None,
    tenant_query_fallback: None,
    next_probe: PageProbe,
) -> None:
    open_as(page, base_url, "acme", "/notes/")

    bundle = [
        response
        for response in next_probe.responses
        if response.url.endswith("/static/next/next.min.js")
    ]
    assert [response.url for response in bundle] == [
        f"{base_url}/_t/acme/static/next/next.min.js"
    ]
    assert [response.status for response in bundle] == [200]
    assert page.evaluate("() => typeof window.Next") == "function"
    expect(page.locator(NOTE_CARD)).to_have_count(2)


def test_switching_the_tenant_swaps_the_notes_and_the_accent(
    page: Page, base_url: str, demo_data: None, tenant_query_fallback: None
) -> None:
    open_as(page, base_url, "acme", "/notes/")

    expect(page).to_have_url(f"{base_url}/notes/")
    expect(page.locator(NOTE_CARD)).to_have_count(2)
    expect(page.get_by_role("heading", name="Welcome to Acme")).to_be_visible()
    expect(page.get_by_role("heading", name="Globex roadmap")).to_have_count(0)
    expect(page.locator("h1.accent-text")).to_have_css("color", ACME_ACCENT)

    open_as(page, base_url, "globex", "/notes/")

    expect(page).to_have_url(f"{base_url}/notes/")
    expect(page.locator(NOTE_CARD)).to_have_count(1)
    expect(page.get_by_role("heading", name="Globex roadmap")).to_be_visible()
    expect(page.get_by_role("heading", name="Welcome to Acme")).to_have_count(0)
    expect(page.locator("h1.accent-text")).to_have_css("color", GLOBEX_ACCENT)


def test_co_located_assets_load_under_the_tenant_prefix(
    page: Page,
    base_url: str,
    acme_note: Note,
    tenant_query_fallback: None,
    next_probe: PageProbe,
) -> None:
    open_as(page, base_url, "acme", f"/notes/{acme_note.pk}/edit/")
    expect(page.locator(PREVIEW)).to_be_visible()

    prefix = f"{base_url}/_t/acme/static/"
    requested = same_origin_static(next_probe, base_url)
    prefixed = [url for url in requested if url.startswith(prefix)]
    unprefixed = [url for url in requested if url.startswith(f"{base_url}/static/")]

    assert len(prefixed) + len(unprefixed) == len(requested)
    assert unprefixed == []
    served = {url.removeprefix(prefix) for url in prefixed}
    assert "next/next.min.js" in served
    assert "next/components/markdown_preview.mjs" in served
    assert "next/components/markdown_preview.css" in served
    assert "shared/css/tokens.css" in served
    statuses = {
        response.status
        for response in next_probe.responses
        if response.url.startswith(prefix)
    }
    assert statuses == {200}


def test_typing_in_the_editor_updates_the_markdown_preview(
    page: Page,
    base_url: str,
    demo_data: None,
    tenant_query_fallback: None,
    next_probe: PageProbe,
) -> None:
    open_as(page, base_url, "acme", "/notes/new/")
    page.wait_for_function("() => window.marked !== undefined")

    expect(page.locator(PREVIEW)).to_have_text("Nothing to preview yet.")

    page.locator("#id_body").press_sequentially("# Draft", delay=20)

    expect(page.locator(f"{PREVIEW} h1")).to_have_text("Draft")

    page.locator("#id_body").fill("- one\n- two\n")

    expect(page.locator(f"{PREVIEW} li")).to_have_count(2)
    expect(page.locator(f"{PREVIEW} h1")).to_have_count(0)
    assert next_probe.partial_requests() == []


def test_a_locked_note_refuses_every_save_path(
    page: Page,
    base_url: str,
    locked_note: Note,
    tenant_query_fallback: None,
    next_probe: PageProbe,
) -> None:
    open_as(page, base_url, "acme", f"/notes/{locked_note.pk}/edit/")

    expect(page.get_by_text("Locked note")).to_be_visible()
    save = page.get_by_role("button", name="Save note")
    expect(save).to_be_disabled()

    page.locator("#id_body").fill("smuggled past the guard")
    page.locator("#id_title").press("Enter")

    with page.expect_navigation():
        page.get_by_role("link", name="Back to notes").click()

    expect(page).to_have_url(f"{base_url}/notes/")
    assert next_probe.partial_requests() == []
    locked_note.refresh_from_db()
    assert locked_note.body != "smuggled past the guard"


def test_saving_an_unlocked_note_lands_back_on_its_editor(
    page: Page, base_url: str, acme_note: Note, tenant_query_fallback: None
) -> None:
    open_as(page, base_url, "acme", f"/notes/{acme_note.pk}/edit/")

    save = page.get_by_role("button", name="Save note")
    expect(save).to_be_enabled()

    page.locator("#id_body").fill("rewritten in the browser")
    with page.expect_navigation():
        save.click()

    expect(page).to_have_url(f"{base_url}/notes/{acme_note.pk}/edit/")
    expect(page.locator("#id_body")).to_have_value("rewritten in the browser")
    acme_note.refresh_from_db()
    assert acme_note.body == "rewritten in the browser"
