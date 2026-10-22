import pytest
from access.models import AccessRequest, AuditEntry
from e2e_support.browser import (
    PageProbe,
    applied_count,
    assert_same_document,
    mark_document,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Locator, Page, expect


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
LANDING_TITLE = "next.dj — Audit-trail forms"
POLL_TIMEOUT_MS = 12_000


def filed_request() -> AccessRequest:
    access_request = AccessRequest.objects.create(
        full_name="Ada Lovelace",
        email="ada@example.com",
        team="Computing",
        project_slug="engine",
        reason="Need read access for analysis.",
        expires_in_days=14,
    )
    AuditEntry.objects.create(
        action_name="access_request_wizard",
        kind=AuditEntry.KIND_DISPATCHED,
        source=AuditEntry.SOURCE_BACKEND,
        request=access_request,
        response_status=302,
    )
    return access_request


def open_layer(page: Page, link: Locator, body: str) -> Locator:
    seen = applied_count(page)
    link.click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    wait_for_apply(page, seen)
    expect(dialog.locator(body)).to_be_visible()
    expect(page.locator(f"{DIALOG}:modal")).to_have_count(1)
    return dialog


def open_audit_peek(page: Page, base_url: str) -> Locator:
    page.goto(base_url)
    wait_for_runtime(page)
    mark_document(page)
    link = page.get_by_role("link", name="pending · audit")
    return open_layer(page, link, "[data-audit-peek]")


def test_a_request_row_opens_its_trail_as_a_modal_over_the_landing(
    page: Page, base_url: str
) -> None:
    access_request = filed_request()
    pk = access_request.pk

    dialog = open_audit_peek(page, base_url)

    assert_same_document(page)
    expect(page).to_have_url(f"{base_url}/request/{pk}/audit/")
    expect(page).to_have_title(f"Audit peek · Request #{pk} · next.dj audit")
    expect(dialog).to_contain_text("Ada Lovelace")
    expect(dialog.locator("[data-audit-peek-row]")).to_have_count(1)
    expect(page.locator("[data-audit-table]")).to_have_count(0)

    dialog.get_by_role("button", name="Close").click()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/")
    expect(page).to_have_title(LANDING_TITLE)
    assert_same_document(page)


def test_back_closes_the_peek_and_a_reload_renders_the_real_trail(
    page: Page, base_url: str
) -> None:
    pk = filed_request().pk
    open_audit_peek(page, base_url)

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/")
    expect(page).to_have_title(LANDING_TITLE)

    open_layer(
        page, page.get_by_role("link", name="pending · audit"), "[data-audit-peek]"
    )
    page.reload()
    wait_for_runtime(page)

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator("[data-audit-table] tbody tr")).to_have_count(1)
    expect(page).to_have_title(f"Request #{pk} · next.dj audit")


def test_the_plain_link_in_the_peek_navigates_to_the_full_page(
    page: Page, base_url: str
) -> None:
    pk = filed_request().pk
    dialog = open_audit_peek(page, base_url)

    dialog.get_by_role("link", name="Open full page").click()

    expect(page).to_have_url(f"{base_url}/request/{pk}/audit/")
    expect(page.locator("[data-audit-table] tbody tr")).to_have_count(1)
    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_title(f"Request #{pk} · next.dj audit")


def test_the_poll_inside_the_peek_brings_in_new_rows(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    access_request = filed_request()
    pk = access_request.pk
    dialog = open_audit_peek(page, base_url)
    rows = dialog.locator("[data-audit-peek-row]")
    expect(rows).to_have_count(1)

    AuditEntry.objects.create(
        action_name="reviewer_note",
        kind=AuditEntry.KIND_DISPATCHED,
        source=AuditEntry.SOURCE_BACKEND,
        request=access_request,
    )

    expect(rows).to_have_count(2, timeout=POLL_TIMEOUT_MS)
    expect(rows.first).to_contain_text("reviewer_note")
    expect(dialog.locator("[data-audit-peek]")).to_be_visible()
    expect(page).to_have_title(f"Audit peek · Request #{pk} · next.dj audit")
    expect(page).to_have_url(f"{base_url}/request/{pk}/audit/")

    polls = next_probe.partial_requests("audit-peek-entries")
    assert polls
    assert {response.url for response in polls} == {f"{base_url}/request/{pk}/audit/"}
    assert {response.status for response in polls} == {200}


def test_a_log_row_opens_its_request_over_the_audit_log(
    page: Page, base_url: str
) -> None:
    pk = filed_request().pk
    page.goto(f"{base_url}/admin/audit/")
    wait_for_runtime(page)
    expect(page.locator("[data-audit-table] tbody tr")).to_have_count(1)

    dialog = open_layer(
        page, page.get_by_role("link", name=f"request #{pk}"), "[data-audit-log-peek]"
    )

    expect(page).to_have_url(f"{base_url}/request/{pk}/audit/")
    expect(page).to_have_title(f"Request #{pk} from the log · next.dj audit")
    expect(dialog).to_contain_text("Need read access for analysis.")

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/admin/audit/")
    expect(page).to_have_title("Audit log · next.dj audit")


def test_the_receipt_of_a_render_bodied_page_opens_over_the_trail(
    page: Page, base_url: str
) -> None:
    pk = filed_request().pk
    page.goto(f"{base_url}/request/{pk}/audit/")
    wait_for_runtime(page)
    mark_document(page)

    dialog = open_layer(
        page, page.get_by_role("link", name="View the receipt"), "[data-receipt-peek]"
    )

    assert_same_document(page)
    expect(page).to_have_url(f"{base_url}/request/{pk}/receipt/")
    expect(page).to_have_title(f"Receipt preview · Request #{pk} · next.dj audit")
    expect(dialog).to_contain_text(f"AR-{pk:05d}")

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator("[data-receipt]")).to_contain_text(f"AR-{pk:05d}")
    expect(page).to_have_title(f"Receipt · Request #{pk} · next.dj audit")
