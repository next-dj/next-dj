import pytest
from e2e_support.browser import PageProbe, wait_for_runtime
from kanban.models import Board, Card, Column
from playwright.sync_api import Locator, Page, Response, Route, expect


pytestmark = pytest.mark.e2e

ISLAND = "#kanban-board"
COLUMN = "[data-kanban-column]"
SERVER_CARD_LIST = "[data-kanban-cards]"
CARD = "[data-kanban-card]"
PENDING_CARD = "[data-kanban-card-pending]"
WIP_BADGE = "[data-kanban-wip]"
ALERT = "[data-kanban-error]"
BAD_REQUEST = "400 (Bad Request)"

TAG_ISLAND = f"() => {{ document.querySelector('{ISLAND}').keptItsNode = true; }}"
READ_ISLAND_TAG = f"() => document.querySelector('{ISLAND}').keptItsNode"
HOLD_DETACHED_ISLAND = (
    f"() => {{ window.__detached = document.querySelector('{ISLAND}'); }}"
)
COUNT_DETACHED_CHILDREN = "() => window.__detached.childElementCount"

REPLACE_ISLAND_WITH_A_FRESH_SERVER_RENDER = """
async () => {
  const response = await fetch(window.location.href);
  const parsed = new DOMParser().parseFromString(await response.text(), "text/html");
  window.Next.partial.apply({
    version: "v1",
    ops: [
      {
        op: "replace",
        target: { css: "#kanban-board" },
        html: parsed.querySelector("#kanban-board").outerHTML,
      },
    ],
    assets: [],
    form: null,
  });
}
"""


@pytest.fixture()
def board(demo_data: None) -> Board:
    return Board.objects.get(slug="engineering-roadmap")


@pytest.fixture()
def other_board(demo_data: None) -> Board:
    return Board.objects.get(slug="marketing-launch")


@pytest.fixture()
def backlog(board: Board) -> Column:
    return board.columns.get(title="Backlog")


@pytest.fixture()
def in_progress(board: Board) -> Column:
    return board.columns.get(title="In progress")


@pytest.fixture()
def done(board: Board) -> Column:
    return board.columns.get(title="Done")


def is_action_post(response: Response) -> bool:
    return response.request.method == "POST"


def open_board(page: Page, base_url: str, board: Board) -> None:
    page.goto(f"{base_url}/board/{board.pk}/")
    wait_for_runtime(page)
    expect(page.locator(SERVER_CARD_LIST)).to_have_count(0)


def header_count(page: Page) -> Locator:
    return page.get_by_role("banner").get_by_text("active boards")


def column_of(page: Page, column: Column) -> Locator:
    return page.locator(f"[data-kanban-column='{column.pk}']")


def card_of(page: Page, card: Card) -> Locator:
    return page.locator(f"[data-kanban-card='{card.pk}']")


def type_card(page: Page, column: Column, title: str) -> None:
    lane = column_of(page, column)
    lane.get_by_role("textbox", name=f"New card in {column.title}").fill(title)
    lane.get_by_role("button", name="Add").click()


def add_card(page: Page, column: Column, title: str) -> None:
    with page.expect_response(is_action_post):
        type_card(page, column, title)
    expect(page.locator(PENDING_CARD)).to_have_count(0)


def move_card(page: Page, card: Locator, target: Locator) -> None:
    with page.expect_response(is_action_post):
        card.drag_to(target)


def drain_expected_rejection(probe: PageProbe, url: str) -> None:
    assert probe.bad_responses == [f"400 {url}"]
    assert [entry for entry in probe.console if BAD_REQUEST not in entry] == []
    probe.bad_responses.clear()
    probe.console.clear()


def test_the_react_island_mounts_over_the_server_rendered_board(
    page: Page, base_url: str, board: Board, next_probe: PageProbe
) -> None:
    open_board(page, base_url, board)

    bundles = [
        response
        for response in next_probe.responses
        if "/static/kanban/dist/assets/" in response.url
    ]
    assert bundles
    assert {response.status for response in bundles} == {200}
    assert page.evaluate("() => typeof window.Next") == "function"
    expect(page.locator(COLUMN)).to_have_count(4)
    expect(page.locator(WIP_BADGE)).to_have_count(2)
    expect(page.get_by_role("textbox", name="New card in Backlog")).to_have_count(1)
    assert next_probe.partial_requests() == []


def test_dragging_a_card_moves_it_and_the_move_survives_a_reload(
    page: Page, base_url: str, board: Board, backlog: Column, done: Column
) -> None:
    open_board(page, base_url, board)
    card = backlog.cards.get(title="Audit static pipeline")
    expect(column_of(page, backlog).locator(CARD)).to_have_count(2)
    expect(column_of(page, done).locator(CARD)).to_have_count(1)

    move_card(page, card_of(page, card), column_of(page, done))

    expect(column_of(page, done).locator(CARD)).to_have_count(2)
    expect(column_of(page, backlog).locator(CARD)).to_have_count(1)

    page.reload()
    wait_for_runtime(page)
    expect(page.locator(SERVER_CARD_LIST)).to_have_count(0)

    expect(column_of(page, done).locator(CARD)).to_have_count(2)
    expect(column_of(page, done).locator(CARD).last).to_contain_text(card.title)
    expect(column_of(page, backlog).locator(CARD)).to_have_count(1)
    card.refresh_from_db()
    assert card.column_id == done.pk


def test_a_move_the_server_rejects_rolls_the_card_back_and_alerts(
    page: Page,
    base_url: str,
    board: Board,
    backlog: Column,
    done: Column,
    next_probe: PageProbe,
) -> None:
    open_board(page, base_url, board)
    card = backlog.cards.get(title="Audit static pipeline")
    action_url = page.evaluate("() => window.Next.context.board.move_card_url")
    Card.objects.filter(pk=card.pk).delete()

    move_card(page, card_of(page, card), column_of(page, done))

    expect(page.locator(ALERT)).to_contain_text("Move rejected by server.")
    expect(column_of(page, backlog).locator(CARD)).to_have_count(2)
    expect(column_of(page, done).locator(CARD)).to_have_count(1)
    expect(column_of(page, backlog).locator(CARD).first).to_contain_text(card.title)
    drain_expected_rejection(next_probe, f"{base_url}{action_url}")

    page.get_by_role("button", name="Dismiss").click()

    expect(page.locator(ALERT)).to_have_count(0)


def test_a_new_card_shows_as_pending_until_the_server_names_it(
    page: Page, base_url: str, board: Board, backlog: Column
) -> None:
    open_board(page, base_url, board)
    action_url = page.evaluate("() => window.Next.context.board.create_card_url")
    held: list[Route] = []

    def hold(route: Route) -> None:
        held.append(route)

    page.route(f"**{action_url}", hold)

    with page.expect_request(f"**{action_url}"):
        type_card(page, backlog, "Write the runbook")

    expect(page.locator(PENDING_CARD)).to_have_count(1)
    expect(page.locator(PENDING_CARD)).to_contain_text("Write the runbook")
    assert page.locator(PENDING_CARD).get_attribute("draggable") == "false"
    assert Card.objects.filter(title="Write the runbook").count() == 0

    with page.expect_response(is_action_post):
        held[0].continue_()

    expect(page.locator(PENDING_CARD)).to_have_count(0)
    created = Card.objects.get(title="Write the runbook")
    expect(card_of(page, created)).to_have_count(1)
    page.unroute(f"**{action_url}")

    page.reload()
    wait_for_runtime(page)
    expect(page.locator(SERVER_CARD_LIST)).to_have_count(0)

    expect(column_of(page, backlog).locator(CARD)).to_have_count(3)
    expect(column_of(page, backlog).locator(CARD).last).to_contain_text(created.title)


def test_a_card_over_the_wip_limit_is_rolled_back_and_the_badge_holds(
    page: Page, base_url: str, board: Board, in_progress: Column, next_probe: PageProbe
) -> None:
    open_board(page, base_url, board)
    badge = column_of(page, in_progress).locator(WIP_BADGE)
    action_url = page.evaluate("() => window.Next.context.board.create_card_url")
    expect(badge).to_have_text("1/2")

    add_card(page, in_progress, "Fills the lane")

    expect(badge).to_have_text("2/2")
    expect(page.locator(ALERT)).to_have_count(0)

    add_card(page, in_progress, "One over the limit")

    expect(page.locator(ALERT)).to_contain_text("Card rejected by server.")
    expect(badge).to_have_text("2/2")
    expect(column_of(page, in_progress).locator(CARD)).to_have_count(2)
    assert Card.objects.filter(title="One over the limit").count() == 0
    drain_expected_rejection(next_probe, f"{base_url}{action_url}")


def test_renaming_and_archiving_keep_the_header_count_agreed_everywhere(
    page: Page, base_url: str, board: Board, other_board: Board
) -> None:
    page.goto(f"{base_url}/board/{board.pk}/settings/")
    wait_for_runtime(page)
    expect(header_count(page)).to_have_text("2 active boards")

    page.fill("#rename-title", "Platform roadmap")
    page.get_by_role("button", name="Save").click()

    expect(page.get_by_role("heading", name="Platform roadmap")).to_be_visible()
    expect(header_count(page)).to_have_text("2 active boards")

    page.goto(f"{base_url}/board/{other_board.pk}/settings/")
    wait_for_runtime(page)
    page.get_by_role("checkbox").check()
    page.get_by_role("button", name="Apply").click()

    expect(page.get_by_role("heading", name="Boards")).to_be_visible()
    expect(header_count(page)).to_have_text("1 active boards")
    expect(page.get_by_text("Platform roadmap")).to_be_visible()
    expect(page.get_by_text(other_board.title)).to_have_count(0)

    page.goto(f"{base_url}/board/{board.pk}/")
    wait_for_runtime(page)

    expect(header_count(page)).to_have_text("1 active boards")
    assert Board.objects.filter(archived=False).count() == 1


def test_a_mount_replay_keeps_the_live_island_and_its_typed_state(
    page: Page, base_url: str, board: Board, backlog: Column
) -> None:
    open_board(page, base_url, board)
    lane = column_of(page, backlog)
    draft = lane.get_by_role("textbox", name="New card in Backlog")
    draft.fill("Half-typed title")
    page.evaluate(TAG_ISLAND)

    page.evaluate("() => window.Next.partial.ready()")

    assert page.evaluate(READ_ISLAND_TAG) is True
    expect(draft).to_have_value("Half-typed title")
    expect(page.locator(COLUMN)).to_have_count(4)
    expect(lane.get_by_role("textbox", name="New card in Backlog")).to_have_count(1)

    with page.expect_response(is_action_post):
        lane.get_by_role("button", name="Add").click()
    expect(page.locator(PENDING_CARD)).to_have_count(0)

    expect(lane.locator(CARD)).to_have_count(3)
    assert Card.objects.filter(title="Half-typed title").count() == 1


def test_replacing_the_island_unmounts_the_old_root_and_mounts_one_new(
    page: Page, base_url: str, board: Board, backlog: Column
) -> None:
    open_board(page, base_url, board)
    page.evaluate(TAG_ISLAND)
    page.evaluate(HOLD_DETACHED_ISLAND)

    page.evaluate(REPLACE_ISLAND_WITH_A_FRESH_SERVER_RENDER)

    expect(page.locator(SERVER_CARD_LIST)).to_have_count(0)
    assert page.evaluate(READ_ISLAND_TAG) is None
    assert page.evaluate(COUNT_DETACHED_CHILDREN) == 0
    expect(page.locator(COLUMN)).to_have_count(4)
    expect(page.get_by_role("textbox", name="New card in Backlog")).to_have_count(1)

    add_card(page, backlog, "Mounted once")

    expect(column_of(page, backlog).locator(CARD)).to_have_count(3)
    assert Card.objects.filter(title="Mounted once").count() == 1
