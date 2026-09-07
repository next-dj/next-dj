import pytest
from access.models import AccessRequest, AuditEntry
from e2e_support.browser import (
    PageProbe,
    applied_count,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Locator, Page, expect


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
REQUEST_LIST = "[data-next-zone='request-list']"
AUDIT_ZONE = "[data-next-zone='audit-table']"
POLICY = "input[name='policy_acknowledged']"
EMAIL_ERROR = "Enter a valid email address."
REQUIRED_ERROR = "This field is required."


def open_wizard(page: Page) -> Locator:
    seen = applied_count(page)
    page.get_by_role("link", name="Start request", exact=True).click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    wait_for_apply(page, seen)
    expect(dialog.locator("#id_full_name")).to_be_visible()
    return dialog


def blur_and_settle(page: Page, field: Locator) -> None:
    seen = applied_count(page)
    field.blur()
    wait_for_apply(page, seen)


def fill_field(page: Page, dialog: Locator, field: str, value: str) -> None:
    dialog.locator(field).fill(value)
    blur_and_settle(page, dialog.locator(field))


def fill_identity(page: Page, dialog: Locator) -> None:
    fill_field(page, dialog, "#id_full_name", "Ada Lovelace")
    fill_field(page, dialog, "#id_email", "ada@example.com")
    fill_field(page, dialog, "#id_team", "Computing")


def fill_scope(page: Page, dialog: Locator) -> None:
    fill_field(page, dialog, "#id_project_slug", "engine")
    fill_field(page, dialog, "#id_reason", "Need read access for analysis.")
    fill_field(page, dialog, "#id_expires_in_days", "14")


def section(dialog: Locator, step: str) -> Locator:
    return dialog.locator(f"[data-step-section='{step}']")


def progress(dialog: Locator, step: str) -> Locator:
    return dialog.locator(f"[data-step='{step}']")


def drain_expected_denial(probe: PageProbe, url: str) -> None:
    assert probe.bad_responses == [f"403 {url}"]
    assert [entry for entry in probe.console if "403 (Forbidden)" not in entry] == []
    probe.bad_responses.clear()
    probe.console.clear()


def test_runtime_boots_and_serves_its_bundle(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)

    bundle = [
        response
        for response in next_probe.responses
        if response.url.endswith("/static/next/next.min.js")
    ]
    assert [response.status for response in bundle] == [200]
    assert page.evaluate("() => typeof window.Next") == "function"


def test_the_opener_shows_a_real_modal_dialog_without_a_reload(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    page.evaluate("() => { window.__stillHere = true; }")

    dialog = open_wizard(page)

    assert dialog.evaluate("element => element.open") is True
    assert dialog.evaluate("element => element.matches(':modal')") is True
    assert page.evaluate("() => window.__stillHere") is True
    expect(page).to_have_url(f"{base_url}/request/identity/")
    expect(section(dialog, "identity")).to_have_attribute("data-state", "active")
    expect(progress(dialog, "identity")).to_have_attribute("data-status", "current")


def test_a_bad_email_blur_marks_only_that_field(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    dialog = open_wizard(page)

    fill_field(page, dialog, "#id_email", "not-an-email")

    email_label = dialog.locator("label").filter(has=page.locator("#id_email"))
    expect(email_label).to_contain_text(EMAIL_ERROR)
    expect(dialog.get_by_text(REQUIRED_ERROR)).to_have_count(0)
    expect(section(dialog, "identity")).to_have_attribute("data-state", "errors")
    assert dialog.evaluate("element => element.open") is True

    probes = [
        response
        for response in next_probe.partial_requests()
        if response.request.headers.get("x-next-validate") == "email"
    ]
    assert [response.status for response in probes] == [200]
    assert AccessRequest.objects.count() == 0


def test_walking_every_step_closes_the_dialog_and_refreshes_the_list(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    page.evaluate("() => { window.__stillHere = true; }")
    expect(page.locator(f"{REQUEST_LIST} li")).to_have_count(0)

    dialog = open_wizard(page)
    fill_field(page, dialog, "#id_email", "nope")
    expect(dialog.get_by_text(EMAIL_ERROR)).to_be_visible()

    fill_identity(page, dialog)
    expect(dialog.get_by_text(EMAIL_ERROR)).to_have_count(0)
    dialog.get_by_role("button", name="Continue").click()

    expect(section(dialog, "scope")).to_have_attribute("data-state", "active")
    expect(progress(dialog, "identity")).to_have_attribute("data-status", "saved")
    expect(section(dialog, "identity")).to_contain_text("Ada Lovelace")
    assert dialog.evaluate("element => element.open") is True

    fill_scope(page, dialog)
    dialog.get_by_role("button", name="Continue").click()

    expect(section(dialog, "approval")).to_have_attribute("data-state", "active")
    expect(progress(dialog, "scope")).to_have_attribute("data-status", "saved")
    expect(dialog.get_by_text("Confirm and submit")).to_be_visible()

    dialog.get_by_role("button", name="Submit request").click()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.locator("[data-next-toast='success']")).to_have_text(
        "Access request submitted"
    )
    expect(page.locator(f"{REQUEST_LIST} li")).to_have_count(1)
    expect(page.locator(REQUEST_LIST)).to_contain_text("ada@example.com")
    expect(page).to_have_url(f"{base_url}/")
    assert page.evaluate("() => window.__stillHere") is True

    access_request = AccessRequest.objects.get()
    assert access_request.email == "ada@example.com"
    assert access_request.expires_in_days == 14
    expect(page.locator(f"[data-next-key='{access_request.pk}']")).to_have_count(1)


def test_escape_dismisses_the_dialog_and_restores_the_url(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    open_wizard(page)
    expect(page).to_have_url(f"{base_url}/request/identity/")

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/")
    expect(page.locator(f"{REQUEST_LIST} li")).to_have_count(0)
    assert AccessRequest.objects.count() == 0


def test_a_backdrop_click_dismisses_the_dialog(page: Page, base_url: str) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    dialog = open_wizard(page)

    dialog.click(position={"x": 2, "y": 2})

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/")


def test_an_unacknowledged_step_is_denied_and_keeps_the_dialog_open(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    dialog = open_wizard(page)
    fill_identity(page, dialog)

    policy = dialog.locator(POLICY)
    policy.uncheck()
    expect(policy).not_to_be_checked()

    blur_and_settle(page, policy)
    expect(policy).not_to_be_checked()

    fill_field(page, dialog, "#id_team", "Analytics")
    expect(policy).not_to_be_checked()
    expect(dialog.locator("#id_team")).to_have_value("Analytics")

    with (
        page.expect_console_message(lambda message: "403" in message.text),
        page.expect_response(
            lambda response: (
                response.request.method == "POST" and response.status == 403
            )
        ) as denial,
    ):
        dialog.get_by_role("button", name="Continue").click()

    drain_expected_denial(next_probe, denial.value.url)
    expect(dialog).to_have_count(1)
    expect(section(dialog, "identity")).to_have_attribute("data-state", "active")
    expect(section(dialog, "scope")).to_have_attribute("data-state", "pending")
    assert AccessRequest.objects.count() == 0

    page.goto(f"{base_url}/request/scope/")
    expect(page.locator("[data-step-section='identity']")).to_have_attribute(
        "data-state", "pending"
    )


def test_the_audit_table_stays_a_skeleton_until_its_zone_is_revealed(
    page: Page, base_url: str
) -> None:
    AuditEntry.objects.create(
        source=AuditEntry.SOURCE_BACKEND,
        kind=AuditEntry.KIND_DISPATCHED,
        action_name="access_request_wizard",
        step="identity",
        response_status=302,
    )
    page.set_viewport_size({"width": 1024, "height": 160})
    page.goto(f"{base_url}/admin/audit/")
    wait_for_runtime(page)

    expect(page.locator("[data-audit-skeleton]")).to_be_visible()
    expect(page.locator("[data-audit-table]")).to_have_count(0)

    seen = applied_count(page)
    page.locator(AUDIT_ZONE).scroll_into_view_if_needed()
    wait_for_apply(page, seen)

    expect(page.locator("[data-audit-table] tbody tr")).to_have_count(1)
    expect(page.locator("[data-audit-skeleton]")).to_have_count(0)
