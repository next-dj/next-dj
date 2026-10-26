import re
from collections.abc import Callable, Iterator

import pytest
from django.contrib.auth.models import User
from e2e_support.browser import (
    CDN_URL_PATTERN,
    PageProbe,
    assert_same_document,
    mark_document,
    wait_for_runtime,
)
from e2e_support.settings import CONTEXT_ARGS
from kanban.demo import DEMO_PASSWORD
from kanban.models import Board, Card, Column
from playwright.sync_api import Browser, Locator, Page, Request, Route, expect


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

ISLAND = "#kanban-board"
CARD = "[data-kanban-card]"
PRESENCE = "[data-kanban-presence]"
MEMBER = "[data-kanban-member]"
MOVING = "is moving this card"
GUEST_NAME = re.compile(r"^Guest \S{4}$")


def stub_cdn(route: Route) -> None:
    if route.request.url.endswith(".css"):
        route.fulfill(status=200, content_type="text/css", body="")
        return
    route.fulfill(
        status=200, content_type="application/javascript", body="window.tailwind = {};"
    )


@pytest.fixture()
def second_tab(page: Page, asgi_server: str) -> Iterator[tuple[Page, PageProbe]]:
    browser: Browser = page.context.browser
    context = browser.new_context(**CONTEXT_ARGS)
    context.route(CDN_URL_PATTERN, stub_cdn)
    other = context.new_page()
    probe = PageProbe()
    probe.attach(other)
    yield other, probe
    probe.detach(other)
    context.close()
    assert probe.problems() == []


def sign_in(page: Page, server: str, username: str) -> None:
    page.goto(f"{server}/sign-in/")
    wait_for_runtime(page)
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(DEMO_PASSWORD)
    page.get_by_role("button", name="Sign in").click()
    expect(page.get_by_role("heading", name="Boards")).to_be_visible()


def watchers(page: Page) -> Locator:
    return page.locator(PRESENCE).locator(MEMBER)


def column_of(page: Page, column: Column) -> Locator:
    return page.locator(f"[data-kanban-column='{column.pk}']")


def card_of(page: Page, card: Card) -> Locator:
    return page.locator(f"[data-kanban-card='{card.pk}']")


def drag_event(page: Page, card: Card, name: str) -> None:
    transfer = page.evaluate_handle("() => new DataTransfer()")
    card_of(page, card).dispatch_event(name, {"dataTransfer": transfer})


@pytest.fixture()
def ada_and_grace(
    page: Page,
    second_tab: tuple[Page, PageProbe],
    asgi_server: str,
    board: Board,
    open_board: Callable[..., None],
) -> tuple[Page, Page]:
    other, _probe = second_tab
    sign_in(page, asgi_server, "ada")
    sign_in(other, asgi_server, "grace")
    open_board(page, asgi_server, board, websocket=True)
    open_board(other, asgi_server, board, websocket=True)
    return page, other


@pytest.fixture()
def two_guests(
    page: Page,
    second_tab: tuple[Page, PageProbe],
    asgi_server: str,
    board: Board,
    open_board: Callable[..., None],
) -> tuple[Page, Page]:
    other, _probe = second_tab
    open_board(page, asgi_server, board, websocket=True)
    open_board(other, asgi_server, board, websocket=True)
    return page, other


def test_both_members_show_up_in_each_tab(ada_and_grace: tuple[Page, Page]) -> None:
    ada, grace = ada_and_grace
    for tab in (ada, grace):
        expect(watchers(tab)).to_have_count(2)
        expect(tab.locator(PRESENCE)).to_contain_text("Ada Lovelace")
        expect(tab.locator(PRESENCE)).to_contain_text("Grace Hopper")
    assert User.objects.filter(username__in=["ada", "grace"]).count() == 2


def test_guests_are_listed_without_signing_in(two_guests: tuple[Page, Page]) -> None:
    for tab in two_guests:
        expect(watchers(tab)).to_have_count(2)
        for member in watchers(tab).all():
            expect(member).to_have_attribute("data-kanban-member", GUEST_NAME)


def test_a_move_in_one_tab_lands_in_the_other_without_a_reload(
    ada_and_grace: tuple[Page, Page],
    next_probe: PageProbe,
    backlog: Column,
    done: Column,
) -> None:
    ada, grace = ada_and_grace
    card = backlog.cards.get(title="Audit static pipeline")
    move_url = ada.evaluate("() => window.Next.context.board.move_card_url")
    posts: list[str] = []

    def record_post(request: Request) -> None:
        if request.method == "POST":
            posts.append(request.url)

    ada.on("request", record_post)
    mark_document(grace)
    seen = len(next_probe.responses)

    card_of(ada, card).drag_to(column_of(ada, done))

    expect(column_of(grace, done).locator(CARD)).to_have_count(2)
    expect(column_of(grace, done).locator(CARD).last).to_contain_text(card.title)
    expect(column_of(grace, backlog).locator(CARD)).to_have_count(1)
    expect(column_of(ada, done).locator(CARD)).to_have_count(2)
    assert_same_document(grace)
    card.refresh_from_db()
    assert card.column_id == done.pk
    assert not [url for url in posts if url.endswith(move_url)]
    assert next_probe.partial_requests("board", since=seen) == []


DROP_CARDS_BACK_TO_BACK = """
([ids, columnId]) => {
  const column = document.querySelector(`[data-kanban-column='${columnId}']`);
  for (const id of ids) {
    const transfer = new DataTransfer();
    transfer.setData("text/card-id", String(id));
    column.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true }));
  }
}
"""


def test_two_cards_dropped_back_to_back_both_reach_the_other_tab(
    ada_and_grace: tuple[Page, Page], backlog: Column, done: Column
) -> None:
    ada, grace = ada_and_grace
    first = backlog.cards.get(title="Audit static pipeline")
    second = backlog.cards.get(title="Document KindRegistry")

    ada.evaluate(DROP_CARDS_BACK_TO_BACK, [[first.pk, second.pk], done.pk])

    expect(column_of(grace, done).locator(CARD)).to_have_count(3)
    expect(card_of(grace, first)).to_be_visible()
    expect(card_of(grace, second)).to_be_visible()
    expect(column_of(grace, backlog).locator(CARD)).to_have_count(0)
    for card in (first, second):
        card.refresh_from_db()
        assert card.column_id == done.pk


def test_a_card_someone_drags_is_marked_in_the_other_tab(
    ada_and_grace: tuple[Page, Page], backlog: Column
) -> None:
    ada, grace = ada_and_grace
    card = backlog.cards.get(title="Audit static pipeline")
    expect(watchers(ada)).to_have_count(2)

    drag_event(ada, card, "dragstart")

    expect(card_of(grace, card)).to_contain_text("Ada Lovelace is moving this card")
    expect(card_of(ada, card)).not_to_contain_text(MOVING)

    drag_event(ada, card, "dragend")

    expect(card_of(grace, card)).not_to_contain_text(MOVING)


def test_a_guest_drag_skips_its_own_echo_and_names_the_guest_elsewhere(
    two_guests: tuple[Page, Page], backlog: Column
) -> None:
    first, second = two_guests
    card = backlog.cards.get(title="Audit static pipeline")
    expect(watchers(second)).to_have_count(2)

    drag_event(first, card, "dragstart")

    expect(card_of(second, card)).to_contain_text(
        re.compile(rf"^.*Guest \S{{4}} {MOVING}")
    )
    expect(card_of(first, card)).not_to_contain_text(MOVING)


def test_the_mark_clears_when_the_member_dragging_leaves(
    ada_and_grace: tuple[Page, Page], backlog: Column
) -> None:
    ada, grace = ada_and_grace
    card = backlog.cards.get(title="Audit static pipeline")
    expect(watchers(ada)).to_have_count(2)
    drag_event(grace, card, "dragstart")
    expect(card_of(ada, card)).to_contain_text("Grace Hopper is moving this card")

    grace.goto("about:blank")

    expect(watchers(ada)).to_have_count(1)
    expect(card_of(ada, card)).not_to_contain_text(MOVING)
