import re
from collections.abc import Callable

import pytest
from django.db.models import F
from e2e_support.browser import (
    PageProbe,
    expect_no_partial_request,
    request_baseline,
    wait_for_runtime,
)
from playwright.sync_api import Page, expect
from polls.models import Choice, Poll

from next.partial import Patches
from next.realtime import publish


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

ANNOUNCEMENT = "[data-poll-vote-announcement]"
WATCHERS = "[data-poll-watchers]"
TOTAL = "[data-poll-chart-total]"
RECORD_GAPS = """() => {
    window.gaps = [];
    window.Next.on("next:stream-gap", (gap) => window.gaps.push(gap));
}"""
COUNT_GAPS = "() => window.gaps.length"
STREAM = re.compile(r"/stream/$")
WEBSOCKET = re.compile(r"/_next/ws/$")


def record[T](page: Page, event: str) -> list[T]:
    seen: list[T] = []

    def collect(item: T) -> None:
        seen.append(item)

    page.on(event, collect)
    return seen


def open_poll(page: Page, base_url: str, poll: Poll) -> None:
    page.goto(f"{base_url}/polls/{poll.pk}/")
    wait_for_runtime(page)
    expect(page.locator(WATCHERS)).to_be_visible()


def cast_vote(poll: Poll, choice: Choice) -> int:
    Choice.objects.filter(pk=choice.pk).update(votes=F("votes") + 1)
    total = sum(poll.choices.values_list("votes", flat=True))
    publish(
        f"poll:{poll.pk}",
        Patches(None).event("poll:votes", {"total_votes": total}),
        on_commit=False,
    )
    return total


def test_pages_negotiate_the_websocket_by_default(
    page: Page, base_url: str, tabs_or_spaces: Poll
) -> None:
    opened = record(page, "websocket")
    open_poll(page, base_url, tabs_or_spaces)
    expect(page.locator(WATCHERS)).to_have_text("1 watching")
    assert [socket.url for socket in opened if WEBSOCKET.search(socket.url)]


def test_pages_fall_back_to_server_sent_events_without_the_websocket(
    page: Page, base_url: str, tabs_or_spaces: Poll, sse_only: None
) -> None:
    opened = record(page, "websocket")
    with page.expect_response(
        lambda response: (
            bool(STREAM.search(response.url))
            and "text/event-stream" in response.headers.get("content-type", "")
        )
    ):
        open_poll(page, base_url, tabs_or_spaces)
    expect(page.locator(WATCHERS)).to_have_text("1 watching")
    assert not opened


def test_the_watcher_count_follows_the_open_tabs(
    page: Page, base_url: str, tabs_or_spaces: Poll, transport: str
) -> None:
    open_poll(page, base_url, tabs_or_spaces)
    expect(page.locator(WATCHERS)).to_have_text("1 watching")

    second = page.context.new_page()
    open_poll(second, base_url, tabs_or_spaces)
    expect(page.locator(WATCHERS)).to_have_text("2 watching")
    expect(second.locator(WATCHERS)).to_have_text("2 watching")

    second.close()
    expect(page.locator(WATCHERS)).to_have_text("1 watching")


def test_an_offline_tab_catches_up_through_replay_without_a_zone_get(
    page: Page,
    next_probe: PageProbe,
    base_url: str,
    tabs_or_spaces: Poll,
    transport: str,
    cut_network: Callable[[], None],
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    open_poll(page, base_url, tabs_or_spaces)
    cast_vote(tabs_or_spaces, tabs)
    expect(page.locator(ANNOUNCEMENT)).to_have_text("1 total votes")
    expect(page.locator(TOTAL)).to_have_text("0")
    page.evaluate(RECORD_GAPS)
    gaps = page.evaluate(COUNT_GAPS)
    baseline = request_baseline(page, next_probe)
    requests = record(page, "request")
    sockets = record(page, "websocket")

    page.context.set_offline(True)
    cut_network()
    cast_vote(tabs_or_spaces, tabs)
    cast_vote(tabs_or_spaces, tabs)
    page.context.set_offline(False)

    expect(page.locator(ANNOUNCEMENT)).to_have_text("3 total votes")
    expect_no_partial_request(page, next_probe, baseline)
    fetched = [
        request.url
        for request in requests
        if request.resource_type in {"document", "fetch", "xhr"}
        and not STREAM.search(request.url)
    ]
    assert fetched == []
    if transport == "websocket":
        assert len(sockets) == 1
    assert page.evaluate(COUNT_GAPS) == gaps
    next_probe.console.clear()
    next_probe.failed_requests.clear()
