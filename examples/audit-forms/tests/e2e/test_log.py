import re

import pytest
from access.models import AuditEntry
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

SENTINEL_BELOW_THE_FOLD = {"width": 1280, "height": 360}
BATCH = 20

ROWS = "[data-next-zone='audit-rows'] tr"
SENTINEL = "[data-next-key='audit-rows-more']"
SHOW_OLDER = "a[data-next-key='audit-rows-more']"
SHOW_NEWER = "a[data-next-key='audit-rows-less']:not([hidden])"
END_MARKER = "[data-next-key='audit-rows-more'] [data-audit-end]"
WINDOW_URL = re.compile(r"/admin/audit/\?cursor=[\w-]+&_restore=cursor$")


def log_keys(**filters: str) -> list[str]:
    rows = AuditEntry.objects.filter(**filters).order_by("-created_at", "-pk")
    return [str(pk) for pk in rows.values_list("pk", flat=True)]


def row(page: Page, key: str) -> Locator:
    return page.locator(f"{ROWS}[data-next-key='{key}']")


def open_log(page: Page, base_url: str) -> None:
    page.set_viewport_size(SENTINEL_BELOW_THE_FOLD)
    page.goto(f"{base_url}/admin/audit/")
    wait_for_runtime(page)
    expect(page.locator(ROWS)).to_have_count(BATCH)


def load_older(page: Page, last_key: str, total: int) -> None:
    page.locator(SENTINEL).scroll_into_view_if_needed()
    expect(page.locator(ROWS).last).to_have_attribute("data-next-key", last_key)
    expect(page.locator(ROWS)).to_have_count(total)


def scroll_past_the_prune(page: Page, keys: list[str]) -> None:
    for batch in (2, 3):
        load_older(page, keys[batch * BATCH - 1], batch * BATCH)
    load_older(page, keys[4 * BATCH - 1], 3 * BATCH)


def test_revealing_the_sentinel_appends_the_next_batch_without_a_reload(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    keys = log_keys()
    open_log(page, base_url)
    mark_document(page)

    load_older(page, keys[2 * BATCH - 1], 2 * BATCH)

    assert_same_document(page)
    expect(page.locator(ROWS).first).to_have_attribute("data-next-key", keys[0])
    expect(page).to_have_url(WINDOW_URL)
    expect(page.locator(SHOW_NEWER)).to_have_count(0)
    expect(page.locator(SHOW_OLDER)).to_have_attribute("data-next-merge", "append")
    fetched = next_probe.partial_requests(zone="audit-rows")
    assert len(fetched) == 1
    assert "cursor=" in fetched[0].url


def test_a_prune_keeps_the_scroll_position_and_re_arms_the_newer_link(
    page: Page, base_url: str, demo_data: None
) -> None:
    keys = log_keys()
    open_log(page, base_url)
    for batch in (2, 3):
        load_older(page, keys[batch * BATCH - 1], batch * BATCH)
    expect(page.locator(SHOW_NEWER)).to_have_count(0)
    anchor = row(page, keys[3 * BATCH - 1])

    page.locator(SENTINEL).scroll_into_view_if_needed()
    expect(anchor).to_be_in_viewport()
    expect(page.locator(ROWS).last).to_have_attribute(
        "data-next-key", keys[4 * BATCH - 1]
    )
    expect(page.locator(ROWS)).to_have_count(3 * BATCH)

    expect(page.locator(ROWS).first).to_have_attribute("data-next-key", keys[BATCH])
    expect(row(page, keys[0])).to_have_count(0)
    expect(anchor).to_be_in_viewport()
    expect(page.locator(SHOW_NEWER)).to_have_count(1)
    expect(page.locator(SHOW_NEWER)).to_have_attribute("href", re.compile(r"cursor="))


def test_the_re_armed_link_brings_the_pruned_batch_back(
    page: Page, base_url: str, demo_data: None
) -> None:
    keys = log_keys()
    open_log(page, base_url)
    scroll_past_the_prune(page, keys)

    page.locator(SHOW_NEWER).click()

    expect(page.locator(ROWS)).to_have_count(4 * BATCH)
    expect(page.locator(ROWS).first).to_have_attribute("data-next-key", keys[0])
    expect(page.locator(SHOW_NEWER)).to_have_count(0)


def test_a_reload_restores_the_window_the_address_names(
    page: Page, base_url: str, demo_data: None
) -> None:
    keys = log_keys()
    open_log(page, base_url)
    load_older(page, keys[2 * BATCH - 1], 2 * BATCH)
    expect(page).to_have_url(WINDOW_URL)

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(ROWS)).to_have_count(2 * BATCH)
    expect(page.locator(ROWS).first).to_have_attribute("data-next-key", keys[0])
    expect(page.locator(ROWS).last).to_have_attribute(
        "data-next-key", keys[2 * BATCH - 1]
    )
    expect(page.locator(SHOW_NEWER)).to_have_count(0)


def test_a_reload_after_a_prune_restores_the_batches_it_kept(
    page: Page, base_url: str, demo_data: None
) -> None:
    keys = log_keys()
    open_log(page, base_url)
    scroll_past_the_prune(page, keys)
    expect(page).to_have_url(WINDOW_URL)

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(ROWS)).to_have_count(3 * BATCH)
    expect(page.locator(ROWS).first).to_have_attribute("data-next-key", keys[BATCH])
    expect(page.locator(ROWS).last).to_have_attribute(
        "data-next-key", keys[4 * BATCH - 1]
    )
    expect(page.locator(SHOW_NEWER)).to_have_count(1)


def test_the_last_batch_swaps_the_link_for_the_closing_note(
    page: Page, base_url: str, demo_data: None
) -> None:
    keys = log_keys()
    open_log(page, base_url)
    scroll_past_the_prune(page, keys)

    page.locator(SENTINEL).scroll_into_view_if_needed()

    expect(page.locator(ROWS).last).to_have_attribute("data-next-key", keys[-1])
    expect(page.locator(SHOW_OLDER)).to_have_count(0)
    expect(page.locator(END_MARKER)).to_have_text("End of the log.")


def test_a_new_filter_resets_the_window_and_the_address(
    page: Page, base_url: str, demo_data: None
) -> None:
    keys = log_keys()
    failed = log_keys(kind=AuditEntry.KIND_VALIDATION_FAILED)
    open_log(page, base_url)
    load_older(page, keys[2 * BATCH - 1], 2 * BATCH)
    expect(page).to_have_url(WINDOW_URL)

    seen = applied_count(page)
    page.locator("#kind-filter").select_option("validation_failed")
    wait_for_apply(page, seen)

    expect(page.locator(ROWS)).to_have_count(len(failed))
    expect(page.locator(ROWS).first).to_have_attribute("data-next-key", failed[0])
    expect(page).to_have_url(f"{base_url}/admin/audit/?kind=validation_failed")
    expect(page.locator(END_MARKER)).to_have_text("End of the log.")
