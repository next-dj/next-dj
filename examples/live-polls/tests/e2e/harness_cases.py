from contextlib import suppress

import pytest
from e2e_support.browser import wait_for_runtime
from playwright.sync_api import Error, Page
from polls.models import Poll


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

DELIBERATE_FAILURE = "the case failed on purpose while its stream was open"


def open_poll(page: Page, base_url: str, poll: Poll) -> None:
    page.goto(f"{base_url}/polls/{poll.pk}/")
    wait_for_runtime(page)


def test_fails_while_its_stream_is_open(
    page: Page, base_url: str, tabs_or_spaces: Poll
) -> None:
    open_poll(page, base_url, tabs_or_spaces)
    pytest.fail(DELIBERATE_FAILURE)


def test_passes_beside_a_crashed_tab(
    page: Page, base_url: str, tabs_or_spaces: Poll
) -> None:
    open_poll(page, base_url, tabs_or_spaces)
    crashed = page.context.new_page()
    open_poll(crashed, base_url, tabs_or_spaces)
    with suppress(Error):
        crashed.goto("chrome://crash")
    assert page.locator("[data-poll-chart-votes]").first.inner_text() == "0"
