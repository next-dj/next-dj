import pytest
from e2e_support.browser import PageProbe, wait_for_runtime
from flags.models import Flag
from playwright.sync_api import Locator, Page, expect


pytestmark = pytest.mark.e2e

CHECKBOX = "input[name='enabled_names']"
DENIAL_STAT = "form permission denials"


def open_panel(page: Page, base_url: str, path: str) -> None:
    page.goto(f"{base_url}{path}")
    wait_for_runtime(page)


def checkbox(page: Page, name: str) -> Locator:
    return page.locator(f"{CHECKBOX}[value='{name}']")


def toggle_state(page: Page, name: str) -> Locator:
    return page.locator(f"li:has({CHECKBOX}[value='{name}']) > div > span")


def guard(page: Page, name: str) -> Locator:
    return page.locator(f"[data-feature-guard='{name}']")


def stat_value(page: Page, label: str) -> Locator:
    return page.get_by_role("article").filter(has_text=label).locator("p").nth(1)


def save_toggles(page: Page) -> None:
    with page.expect_navigation():
        page.get_by_role("button", name="Save toggles").click()


def deny_save(page: Page, probe: PageProbe) -> None:
    with (
        page.expect_console_message(lambda message: "403" in message.text),
        page.expect_response(
            lambda response: (
                response.request.method == "POST" and response.status == 403
            )
        ) as denial,
    ):
        page.get_by_role("button", name="Save toggles").click()

    assert probe.bad_responses == [f"403 {denial.value.url}"]
    assert [entry for entry in probe.console if "403 (Forbidden)" not in entry] == []
    probe.bad_responses.clear()
    probe.console.clear()


def test_runtime_boots_and_serves_its_bundle(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_panel(page, base_url, "/admin/")

    bundle = [
        response
        for response in next_probe.responses
        if response.url.endswith("/static/next/next.min.js")
    ]
    assert [response.status for response in bundle] == [200]
    assert page.evaluate("() => typeof window.Next") == "function"
    expect(page.locator(CHECKBOX)).to_have_count(4)


def test_saving_the_bulk_toggle_survives_a_reload(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_panel(page, base_url, "/admin/")
    expect(checkbox(page, "dark_sidebar")).not_to_be_checked()
    expect(checkbox(page, "ai_suggestions")).not_to_be_checked()
    expect(toggle_state(page, "dark_sidebar")).to_have_text("off")

    checkbox(page, "dark_sidebar").check()
    checkbox(page, "ai_suggestions").check()
    save_toggles(page)

    expect(page).to_have_url(f"{base_url}/admin/")
    expect(page.get_by_role("alert")).to_have_text("Flag toggles saved.")
    expect(toggle_state(page, "dark_sidebar")).to_have_text("on")
    expect(toggle_state(page, "ai_suggestions")).to_have_text("on")

    page.reload()

    expect(checkbox(page, "dark_sidebar")).to_be_checked()
    expect(checkbox(page, "ai_suggestions")).to_be_checked()
    expect(page.get_by_role("alert")).to_have_count(0)
    assert set(Flag.objects.filter(enabled=True).values_list("name", flat=True)) == {
        "admin_writes",
        "beta_checkout",
        "dark_sidebar",
        "ai_suggestions",
    }


def test_closing_the_write_gate_denies_the_next_save(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_panel(page, base_url, "/admin/")

    checkbox(page, "admin_writes").uncheck()
    save_toggles(page)

    expect(checkbox(page, "admin_writes")).not_to_be_checked()

    checkbox(page, "dark_sidebar").check()
    deny_save(page, next_probe)

    expect(page).to_have_url(f"{base_url}/admin/")
    expect(toggle_state(page, "dark_sidebar")).to_have_text("off")
    assert Flag.objects.get(name="dark_sidebar").enabled is False


def test_enabling_a_flag_reveals_its_guarded_banner(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_panel(page, base_url, "/demo/")

    expect(guard(page, "beta_checkout")).to_be_visible()
    expect(guard(page, "dark_sidebar")).to_be_hidden()
    expect(guard(page, "ai_suggestions")).to_be_hidden()

    open_panel(page, base_url, "/admin/")
    checkbox(page, "dark_sidebar").check()
    save_toggles(page)

    open_panel(page, base_url, "/demo/")

    expect(guard(page, "dark_sidebar")).to_be_visible()
    expect(guard(page, "dark_sidebar")).to_contain_text("Dark sidebar")
    expect(guard(page, "beta_checkout")).to_be_visible()
    expect(guard(page, "ai_suggestions")).to_be_hidden()


def test_a_denied_save_shows_up_in_the_metrics_panel(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_panel(page, base_url, "/admin/metrics/")
    expect(stat_value(page, DENIAL_STAT)).to_have_text("0")

    open_panel(page, base_url, "/admin/")
    checkbox(page, "admin_writes").uncheck()
    save_toggles(page)
    deny_save(page, next_probe)

    open_panel(page, base_url, "/admin/metrics/")

    expect(stat_value(page, DENIAL_STAT)).to_have_text("1")
    expect(page.locator("tbody tr")).to_have_count(2)
    expect(page.locator("tbody tr").first).to_contain_text("admin")
