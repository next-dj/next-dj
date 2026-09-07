from collections.abc import Iterator

import pytest
from e2e_support.browser import (
    CDN_URL_PATTERN,
    PageProbe,
    applied_count,
    wait_for_apply,
    wait_for_runtime,
)
from e2e_support.settings import CONTEXT_ARGS
from playwright.sync_api import Browser, Locator, Page, Response, Route, expect
from polls.broker import broker, build_snapshot
from polls.models import Choice, Poll


pytestmark = pytest.mark.e2e

CHART_APP = "[data-poll-chart-app]"
VUE_ROW = f"{CHART_APP} li[data-just-updated]"
TOTAL = "[data-poll-chart-total]"
STREAM = "/stream/"

TAG_VUE_APP = f"() => {{ document.querySelector('{CHART_APP}').__vue_app__.kept = 1; }}"
READ_VUE_APP = f"() => document.querySelector('{CHART_APP}').__vue_app__?.kept ?? null"


@pytest.fixture()
def tabs_or_spaces(demo_data: None) -> Poll:
    return Poll.objects.get(question="Tabs or spaces?")


@pytest.fixture()
def vim_or_emacs(demo_data: None) -> Poll:
    return Poll.objects.get(question="Vim or Emacs?")


@pytest.fixture()
def watcher(page: Page, tabs_or_spaces: Poll) -> Iterator[Page]:
    """Return a second tab of the same context, watching the poll over SSE.

    A parked source notices the gone client only on its next wake, so the
    teardown publishes once to release the server thread ahead of the timeout.
    """
    second = page.context.new_page()
    probe = PageProbe()
    probe.attach(second)
    yield second
    probe.detach(second)
    second.close()
    broker.publish(build_snapshot(tabs_or_spaces))
    assert probe.problems() == []


@pytest.fixture()
def no_script_page(browser: Browser) -> Iterator[Page]:
    """Return a page with scripting off, the path the vote form must still serve."""
    context = browser.new_context(**CONTEXT_ARGS, java_script_enabled=False)

    def blank(route: Route) -> None:
        route.fulfill(status=200, content_type="text/css", body="")

    context.route(CDN_URL_PATTERN, blank)
    yield context.new_page()
    context.close()


def open_poll(page: Page, base_url: str, poll: Poll) -> None:
    with page.expect_response(lambda response: STREAM in response.url):
        page.goto(f"{base_url}/polls/{poll.pk}/")
    wait_for_runtime(page)
    expect(page.locator(VUE_ROW)).to_have_count(poll.choices.count())


def votes_of(page: Page, choice: Choice) -> Locator:
    return page.locator(
        f"{CHART_APP} li[data-choice-id='{choice.pk}'] [data-poll-chart-votes]"
    )


def row_of(page: Page, choice: Choice) -> Locator:
    return page.locator(f"{CHART_APP} li[data-choice-id='{choice.pk}']")


def bar_of(page: Page, choice: Choice) -> Locator:
    return page.locator(f"{CHART_APP} li[data-choice-id='{choice.pk}'] .poll-chart-bar")


def vote_for(page: Page, choice: Choice) -> None:
    seen = applied_count(page)
    page.get_by_role("button", name=choice.text, exact=True).click()
    wait_for_apply(page, seen)


def vote_posts(probe: PageProbe) -> list[Response]:
    return [
        response
        for response in probe.partial_requests()
        if response.request.method == "POST"
    ]


def test_the_vue_island_mounts_and_draws_the_bars_the_server_never_sizes(
    page: Page, base_url: str, tabs_or_spaces: Poll, next_probe: PageProbe
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    spaces = tabs_or_spaces.choices.get(text="Spaces")
    Choice.objects.filter(pk=tabs.pk).update(votes=3)
    Choice.objects.filter(pk=spaces.pk).update(votes=1)

    open_poll(page, base_url, tabs_or_spaces)

    bundles = [
        response
        for response in next_probe.responses
        if "/static/polls/dist/assets/" in response.url
    ]
    assert bundles
    assert {response.status for response in bundles} == {200}
    expect(votes_of(page, tabs)).to_have_text("3")
    expect(page.locator(TOTAL)).to_have_text("4")
    assert bar_of(page, tabs).evaluate("element => element.style.width") == "75%"
    assert bar_of(page, spaces).evaluate("element => element.style.width") == "25%"
    assert next_probe.partial_requests() == []


def test_voting_repaints_the_zone_through_the_very_same_vue_instance(
    page: Page, base_url: str, tabs_or_spaces: Poll, next_probe: PageProbe
) -> None:
    open_poll(page, base_url, tabs_or_spaces)
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    page.evaluate("() => { window.__stillHere = true; }")
    page.evaluate(TAG_VUE_APP)

    vote_for(page, tabs)

    expect(votes_of(page, tabs)).to_have_text("1")
    expect(page.locator(TOTAL)).to_have_text("1")
    expect(page.locator(VUE_ROW)).to_have_count(tabs_or_spaces.choices.count())
    expect(row_of(page, tabs)).to_have_attribute("data-just-updated", "true")
    assert bar_of(page, tabs).evaluate("element => element.style.width") == "100%"
    assert page.evaluate(READ_VUE_APP) == 1
    assert page.evaluate("() => window.__stillHere") is True
    assert [response.status for response in vote_posts(next_probe)] == [200]
    tabs.refresh_from_db()
    assert tabs.votes == 1


def test_a_vote_in_one_tab_reaches_a_second_tab_over_the_event_stream(
    page: Page, base_url: str, watcher: Page, tabs_or_spaces: Poll
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    spaces = tabs_or_spaces.choices.get(text="Spaces")
    open_poll(page, base_url, tabs_or_spaces)
    open_poll(watcher, base_url, tabs_or_spaces)
    expect(votes_of(watcher, tabs)).to_have_text("0")

    vote_for(page, tabs)

    expect(votes_of(watcher, tabs)).to_have_text("1")
    expect(watcher.locator(TOTAL)).to_have_text("1")
    expect(row_of(watcher, tabs)).to_have_attribute("data-just-updated", "true")
    expect(row_of(watcher, spaces)).to_have_attribute("data-just-updated", "false")
    expect(votes_of(page, tabs)).to_have_text("1")


def test_the_voting_tab_drops_the_echo_of_its_own_change(
    page: Page,
    base_url: str,
    watcher: Page,
    tabs_or_spaces: Poll,
    next_probe: PageProbe,
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    open_poll(page, base_url, tabs_or_spaces)
    open_poll(watcher, base_url, tabs_or_spaces)

    vote_for(page, tabs)

    expect(votes_of(watcher, tabs)).to_have_text("1")
    expect(votes_of(page, tabs)).to_have_text("1")
    expect(page.locator(TOTAL)).to_have_text("1")
    methods = [response.request.method for response in next_probe.partial_requests()]
    assert methods == ["POST"]


def test_a_choice_that_drifted_to_another_poll_is_refused_and_changes_nothing(
    page: Page,
    base_url: str,
    tabs_or_spaces: Poll,
    vim_or_emacs: Poll,
    next_probe: PageProbe,
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    spaces = tabs_or_spaces.choices.get(text="Spaces")
    open_poll(page, base_url, tabs_or_spaces)
    page.evaluate(TAG_VUE_APP)
    Choice.objects.filter(pk=spaces.pk).update(poll=vim_or_emacs)

    vote_for(page, spaces)

    rejected = vote_posts(next_probe)
    assert [response.headers.get("x-next-form") for response in rejected] == ["invalid"]
    expect(page.get_by_role("button", name="Spaces", exact=True)).to_have_count(0)
    expect(page.get_by_role("button", name="Tabs", exact=True)).to_have_count(1)
    expect(votes_of(page, tabs)).to_have_text("0")
    expect(page.locator(TOTAL)).to_have_text("0")
    assert page.evaluate(READ_VUE_APP) == 1
    tabs.refresh_from_db()
    spaces.refresh_from_db()
    assert (tabs.votes, spaces.votes) == (0, 0)


def test_without_scripting_the_server_still_counts_the_vote(
    no_script_page: Page, base_url: str, tabs_or_spaces: Poll
) -> None:
    page = no_script_page
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    page.goto(f"{base_url}/polls/{tabs_or_spaces.pk}/")

    expect(page.locator(VUE_ROW)).to_have_count(0)
    expect(votes_of(page, tabs)).to_have_text("0")

    page.get_by_role("button", name="Tabs", exact=True).click()

    expect(page).to_have_url(f"{base_url}/polls/{tabs_or_spaces.pk}/")
    expect(votes_of(page, tabs)).to_have_text("1")
    expect(page.locator(TOTAL)).to_have_text("1")
    assert bar_of(page, tabs).evaluate("element => element.style.width") == "0%"
    tabs.refresh_from_db()
    assert tabs.votes == 1
