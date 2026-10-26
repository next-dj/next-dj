import asyncio
import base64
import re
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.core.asgi import get_asgi_application
from django.core.management import call_command
from django.urls import reverse
from kanban.demo import DEMO_PASSWORD
from kanban.models import Board, BoardQuerySet, Card, Column
from kanban.realtime import (
    GUEST,
    board_topic,
    can_watch_board,
    dragging,
    identify_member,
    public_profile,
)

from next import realtime
from next.realtime import Upstream, presence
from next.testing import (
    NextClient,
    SseReader,
    WebSocketCommunicator,
    assert_metadata,
    capture_published,
    envelope_of,
    find_anchor,
    find_form,
    init_payload,
    issue_socket_ticket,
    memory_broker,
    override_websocket_mount,
)


pytestmark = pytest.mark.django_db

SUBJECT = "f00dcafe0123456789abcdef"
OTHER_SUBJECT = "c0ffee00aaaabbbbccccdddd"


@pytest.fixture()
def board(db) -> Board:
    """Build a small board with three columns and four cards for e2e flows."""
    b = Board.objects.create(title="Roadmap", slug="roadmap")
    backlog = Column.objects.create(board=b, title="Backlog", position=0)
    progress = Column.objects.create(
        board=b, title="In Progress", position=1, wip_limit=2
    )
    done = Column.objects.create(board=b, title="Done", position=2)
    Card.objects.create(column=backlog, title="Plan", position=0)
    Card.objects.create(column=backlog, title="Spec", position=1)
    Card.objects.create(column=progress, title="Build", position=0)
    Card.objects.create(column=done, title="Ship", position=0)
    return b


@pytest.fixture()
def archived_board(db) -> Board:
    return Board.objects.create(title="Archived", slug="archived", archived=True)


@pytest.fixture()
def backlog(board: Board) -> Column:
    return board.columns.get(title="Backlog")


@pytest.fixture()
def progress(board: Board) -> Column:
    return board.columns.get(title="In Progress")


@pytest.fixture()
def done(board: Board) -> Column:
    return board.columns.get(title="Done")


def _board_html(next_client: NextClient, board: Board) -> str:
    response = next_client.get(f"/board/{board.pk}/")
    assert response.status_code == 200
    return response.content.decode()


def _settings_html(next_client: NextClient, board: Board) -> str:
    response = next_client.get(f"/board/{board.pk}/settings/")
    assert response.status_code == 200
    return response.content.decode()


def _rename_form_block(html: str) -> str:
    blocks = re.findall(r"<form[^>]*>.*?</form>", html, flags=re.DOTALL)
    return next(b for b in blocks if 'name="title"' in b and "wip_limit" not in b)


def _form_action_url(block: str) -> str:
    match = re.search(r'<form action="([^"]+)"', block)
    assert match is not None
    return match.group(1)


def _hidden_fields(block: str) -> dict[str, str]:
    return dict(
        re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', block)
    )


class TestBoardList:
    """The index page lists active boards and hides archived ones."""

    def test_renders_active_boards(
        self, next_client: NextClient, board: Board, archived_board: Board
    ) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert board.title in body

    def test_archived_hidden(
        self, next_client: NextClient, archived_board: Board
    ) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert f'href="/board/{archived_board.pk}/"' not in body

    def test_empty_state(self, next_client: NextClient) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "No active boards" in body


class TestBoardView:
    """The board detail page renders columns and the React mount point."""

    def test_columns_and_cards_render(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert "Backlog" in body
        assert "In Progress" in body
        assert "Done" in body
        assert "Plan" in body
        assert "Ship" in body

    def test_every_column_renders_a_create_card_form(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert body.count('placeholder="New card"') == board.columns.count()
        assert 'name="column"' in body

    def test_nested_layout_chain(self, next_client: NextClient, board: Board) -> None:
        body = _board_html(next_client, board)
        assert "🗂️ next.dj Kanban" in body
        assert "Board #" in body
        assert 'id="kanban-board"' in body

    def test_vite_module_scripts_present(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert re.search(r'<script type="module"', body)

    def test_shared_base_module_present(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert '<script type="module" src="/static/shared/js/base.mjs">' in body

    def test_inherit_context_visible_in_settings(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _settings_html(next_client, board)
        assert board.title in body

    def test_tabs_link_back_to_the_board_and_its_settings(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert f'href="/board/{board.pk}/"' in body
        assert f'href="/board/{board.pk}/settings/"' in body

    @pytest.mark.parametrize("path", ["/board/99999/", "/board/99999/settings/"])
    def test_unknown_board_answers_404(
        self, next_client: NextClient, board: Board, path: str
    ) -> None:
        assert next_client.get(path).status_code == 404


class TestSettings:
    """Settings page renders three forms and each one mutates state."""

    def test_get_renders_forms(self, next_client: NextClient, board: Board) -> None:
        body = _settings_html(next_client, board)
        assert "Rename board" in body
        assert "Add column" in body
        assert "Archive" in body

    def test_rename_form_post_redirects(
        self, next_client: NextClient, board: Board
    ) -> None:
        response = next_client.post_action(
            "rename_board_form",
            {"title": "New title"},
            origin=f"/board/{board.pk}/settings/",
        )
        assert response.status_code == 302
        board.refresh_from_db()
        assert board.title == "New title"

    def test_archive_form_toggles(self, next_client: NextClient, board: Board) -> None:
        response = next_client.post_action(
            "archive_board_form",
            {"archived": "1"},
            origin=f"/board/{board.pk}/settings/",
        )
        assert response.status_code == 302
        board.refresh_from_db()
        assert board.archived is True

    def test_archive_unset_redirects_back_to_settings(
        self, next_client: NextClient, board: Board
    ) -> None:
        board.archived = True
        board.save(update_fields=["archived"])
        response = next_client.post_action(
            "archive_board_form", origin=f"/board/{board.pk}/settings/"
        )
        assert response.status_code == 302
        assert response["Location"] == f"/board/{board.pk}/settings/"
        board.refresh_from_db()
        assert board.archived is False

    def test_invalid_then_fixed_resubmit_renames_in_place(
        self, next_client: NextClient, board: Board
    ) -> None:
        rename = _rename_form_block(_settings_html(next_client, board))
        invalid = next_client.post(
            _form_action_url(rename), {**_hidden_fields(rename), "title": ""}
        )
        assert invalid.status_code == 200
        rerendered = _rename_form_block(invalid.content.decode())
        refields = _hidden_fields(rerendered)
        assert refields["_next_form_origin"] == f"/board/{board.pk}/settings/"
        before = Board.objects.count()
        fixed = next_client.post(
            _form_action_url(rerendered), {**refields, "title": "Fixed title"}
        )
        assert fixed.status_code == 302
        assert fixed["Location"] == f"/board/{board.pk}/settings/"
        assert Board.objects.count() == before
        board.refresh_from_db()
        assert board.title == "Fixed title"

    @pytest.mark.parametrize(
        ("action", "data"),
        [
            ("rename_board_form", {"title": "Hijack"}),
            ("archive_board_form", {"archived": "1"}),
            ("create_column_form", {"title": "Hijack"}),
        ],
        ids=["rename", "archive", "create_column"],
    )
    def test_settings_forms_posted_for_an_unknown_board_answer_404(
        self, next_client: NextClient, board: Board, action: str, data: dict
    ) -> None:
        columns = board.columns.count()
        response = next_client.post_action(
            action, data, origin="/board/99999/settings/"
        )
        assert response.status_code == 404
        board.refresh_from_db()
        assert board.title == "Roadmap"
        assert board.archived is False
        assert board.columns.count() == columns

    def test_add_column_form_post(self, next_client: NextClient, board: Board) -> None:
        before = board.columns.count()
        response = next_client.post_action(
            "create_column_form",
            {"title": "New", "wip_limit": "5"},
            origin=f"/board/{board.pk}/settings/",
        )
        assert response.status_code == 302
        assert board.columns.count() == before + 1
        new_column = board.columns.order_by("-position").first()
        assert new_column is not None
        assert new_column.title == "New"
        assert new_column.wip_limit == 5


class TestMoveCard:
    """The move_card form action moves cards between columns."""

    def test_post_moves_card_between_columns(
        self, next_client: NextClient, backlog: Column, done: Column
    ) -> None:
        card = backlog.cards.first()
        response = next_client.post_action(
            "move_card_form",
            {"card_id": card.pk, "target_column": done.pk, "target_position": "0"},
        )
        assert response.status_code == 302
        card.refresh_from_db()
        assert card.column_id == done.pk

    def test_post_normalises_positions(
        self, next_client: NextClient, backlog: Column, done: Column
    ) -> None:
        card = backlog.cards.first()
        next_client.post_action(
            "move_card_form",
            {"card_id": card.pk, "target_column": done.pk, "target_position": "0"},
        )
        positions = list(
            done.cards.order_by("position").values_list("position", flat=True)
        )
        assert positions == list(range(len(positions)))

    def test_cross_board_target_rejected(
        self, next_client: NextClient, board: Board
    ) -> None:
        other_board = Board.objects.create(title="Other", slug="other")
        other_col = Column.objects.create(board=other_board, title="X", position=0)
        card = board.columns.first().cards.first()
        response = next_client.post_action(
            "move_card_form",
            {"card_id": card.pk, "target_column": other_col.pk, "target_position": "0"},
        )
        assert response.status_code == 400
        card.refresh_from_db()
        assert card.column.board_id == board.pk

    @pytest.mark.parametrize(
        "card_id", ["99999", "abc"], ids=["unknown", "non_numeric"]
    )
    def test_forged_card_id_rejected(
        self, next_client: NextClient, done: Column, card_id: str
    ) -> None:
        response = next_client.post_action(
            "move_card_form",
            {"card_id": card_id, "target_column": done.pk, "target_position": "0"},
        )
        assert response.status_code == 400

    def test_negative_position_rejected(
        self, next_client: NextClient, backlog: Column, done: Column
    ) -> None:
        card = backlog.cards.first()
        response = next_client.post_action(
            "move_card_form",
            {"card_id": card.pk, "target_column": done.pk, "target_position": "-1"},
        )
        assert response.status_code == 400


class TestPreviewComponent:
    """The preview composite renders after a successful move."""

    def test_preview_appears_with_moved_query(
        self, next_client: NextClient, board: Board, backlog: Column, done: Column
    ) -> None:
        card = backlog.cards.first()
        next_client.post_action(
            "move_card_form",
            {"card_id": card.pk, "target_column": done.pk, "target_position": "0"},
        )
        response = next_client.get(f"/board/{board.pk}/?moved={card.pk}")
        body = response.content.decode()
        assert "Move complete" in body
        assert f'data-kanban-preview="{card.pk}"' in body

    @pytest.mark.parametrize(
        "query",
        ["", "?moved=99999", "?moved=abc"],
        ids=["no_moved_query", "unknown_card_id", "non_numeric_card_id"],
    )
    def test_preview_hidden_without_a_moved_card(
        self, next_client: NextClient, board: Board, query
    ) -> None:
        response = next_client.get(f"/board/{board.pk}/{query}")
        assert response.status_code == 200
        assert "Move complete" not in response.content.decode()

    def test_a_card_from_another_board_is_not_previewed(
        self, next_client: NextClient, board: Board
    ) -> None:
        secret = Board.objects.create(title="Secret", slug="secret")
        column = Column.objects.create(board=secret, title="Hidden", position=0)
        card = Card.objects.create(column=column, title="Top secret card", position=0)
        response = next_client.get(f"/board/{board.pk}/?moved={card.pk}")
        body = response.content.decode()
        assert response.status_code == 200
        assert "Move complete" not in body
        assert "Top secret card" not in body


class TestCreateCard:
    """The create_card form action appends cards and respects WIP limits."""

    def test_post_appends_card_at_tail(
        self, next_client: NextClient, backlog: Column
    ) -> None:
        before = backlog.cards.count()
        response = next_client.post_action(
            "create_card_form", {"column": backlog.pk, "title": "Extra"}
        )
        assert response.status_code == 302
        backlog.refresh_from_db()
        assert backlog.cards.count() == before + 1
        new = backlog.cards.order_by("-position").first()
        assert new.title == "Extra"
        assert new.position == before

    def test_redirect_names_the_new_card(
        self, next_client: NextClient, board: Board, backlog: Column
    ) -> None:
        response = next_client.post_action(
            "create_card_form", {"column": backlog.pk, "title": "Extra"}
        )
        new = backlog.cards.order_by("-position").first()
        assert response["Location"] == f"/board/{board.pk}/?created={new.pk}"

    def test_wip_limit_blocks_creation(
        self, next_client: NextClient, progress: Column
    ) -> None:
        Card.objects.create(column=progress, title="Second", position=1)
        response = next_client.post_action(
            "create_card_form", {"column": progress.pk, "title": "Over the limit"}
        )
        assert response.status_code == 400
        assert progress.cards.filter(title="Over the limit").count() == 0

    def test_a_column_on_a_board_outside_the_scope_is_refused(
        self, next_client: NextClient, board: Board
    ) -> None:
        secret = Board.objects.create(title="Secret", slug="secret")
        hidden = Column.objects.create(board=secret, title="Hidden", position=0)
        with patch.object(
            BoardQuerySet, "visible_to", lambda qs, _user: qs.exclude(slug="secret")
        ):
            response = next_client.post_action(
                "create_card_form", {"column": hidden.pk, "title": "Injected"}
            )
        assert response.status_code == 400
        assert not hidden.cards.exists()

    def test_wip_limit_none_allows_unlimited(
        self, next_client: NextClient, backlog: Column
    ) -> None:
        for index in range(5):
            response = next_client.post_action(
                "create_card_form", {"column": backlog.pk, "title": f"Card {index}"}
            )
            assert response.status_code == 302
        assert backlog.cards.count() >= 5


class TestJsxBackend:
    """JSX files discovered alongside templates are injected as ES module scripts."""

    def test_page_jsx_rendered_as_module(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert re.search(r'<script type="module" src="[^"]*page\.jsx">', body)

    def test_no_text_babel_scripts_present(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert 'type="text/babel"' not in body


class TestJsContext:
    """`Next._init({...})` carries deep-merged board state."""

    def test_next_init_call_contains_board(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        payload = init_payload(body)
        assert payload["board"]["id"] == board.pk
        assert payload["board"]["title"] == board.title
        assert "csrf" in payload["board"]

    def test_next_init_carries_both_action_urls(
        self, next_client: NextClient, board: Board
    ) -> None:
        payload = init_payload(_board_html(next_client, board))
        assert payload["board"]["move_card_url"]
        assert payload["board"]["create_card_url"]

    def test_deep_merge_columns_present(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        payload = init_payload(body)
        cols = payload["board"]["columns"]
        assert {c["title"] for c in cols} >= {"Backlog", "In Progress", "Done"}
        backlog = next(c for c in cols if c["title"] == "Backlog")
        assert {card["title"] for card in backlog["cards"]} == {"Plan", "Spec"}


class TestInheritedHeaderCount:
    """`active_boards_count` flows from `pages/page.py` into board detail."""

    def test_index_shows_count(self, next_client: NextClient, board: Board) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        match = re.search(r"(\d+) active boards", body)
        assert match is not None
        assert int(match.group(1)) >= 1

    @pytest.mark.parametrize("suffix", ["", "settings/"], ids=["detail", "settings"])
    def test_board_pages_inherit_count(
        self, next_client: NextClient, board: Board, suffix
    ) -> None:
        response = next_client.get(f"/board/{board.pk}/{suffix}")
        assert response.status_code == 200
        assert "active boards" in response.content.decode()


class TestCdnCachePolicy:
    """Local script tags do not carry CDN cache-control attributes."""

    def test_local_script_has_no_cache_attrs(
        self, next_client: NextClient, board: Board
    ) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        match = re.search(
            r'<script src="/static/next/next\.min\.js"[^>]*></script>', body
        )
        assert match is not None
        assert "crossorigin" not in match.group(0)


class TestViteDevAssetsGuard:
    """`@vite/client` is injected only on pages that ship jsx scripts."""

    def test_index_page_skips_vite_client(
        self, next_client: NextClient, board: Board
    ) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert "@vite/client" not in body
        assert 'src="data:text/javascript' not in body

    def test_board_page_includes_vite_client_and_preamble(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        preamble_match = re.search(
            r'<script type="module" src="data:text/javascript;base64,([^"]+)"', body
        )
        assert preamble_match is not None, "React Refresh preamble not injected"
        decoded = base64.b64decode(preamble_match.group(1)).decode()
        assert "RefreshRuntime" in decoded
        assert "@vite/client" in body
        preamble_pos = body.find("data:text/javascript")
        vite_client_pos = body.find("@vite/client")
        page_jsx_pos = body.find("page.jsx")
        assert preamble_pos < vite_client_pos < page_jsx_pos


class TestPayloadEnrichment:
    """Board JS payload carries excerpt and wip_limit for the React layer."""

    def test_payload_includes_wip_limit(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        payload = init_payload(body)
        in_progress = next(
            c for c in payload["board"]["columns"] if c["title"] == "In Progress"
        )
        assert in_progress["wip_limit"] == 2

    def test_payload_includes_card_excerpt(
        self, next_client: NextClient, board: Board, backlog: Column
    ) -> None:
        long_card = backlog.cards.first()
        long_card.body = "y" * 200
        long_card.save(update_fields=["body"])
        body = _board_html(next_client, board)
        payload = init_payload(body)
        backlog_payload = next(
            c for c in payload["board"]["columns"] if c["title"] == "Backlog"
        )
        target = next(c for c in backlog_payload["cards"] if c["id"] == long_card.pk)
        assert target["excerpt"].endswith("…")


def test_index_page_renders_at_the_reversed_root(next_client: NextClient) -> None:
    """The board index answers on the URL the page reverse helper names."""
    response = next_client.get(reverse("next:page_"))
    assert response.status_code == 200
    assert "Boards" in response.content.decode()


class TestPageMetadata:
    """The board callable titles the board and, through `inherit`, its settings."""

    def test_index_uses_the_site_default(self, next_client: NextClient) -> None:
        assert_metadata(next_client.get("/"), title="next.dj — Kanban")

    def test_board_title_comes_from_the_board_provider(
        self, next_client: NextClient, board: Board
    ) -> None:
        assert_metadata(
            _board_html(next_client, board), title="Roadmap · next.dj Kanban"
        )

    def test_settings_inherits_the_board_title(
        self, next_client: NextClient, board: Board
    ) -> None:
        assert_metadata(
            _settings_html(next_client, board), title="Roadmap · next.dj Kanban"
        )


class TestBoardPeek:
    """`(.)board/[int:board]/` serves the `board-peek` zone."""

    def test_the_index_offers_the_peek_beside_the_plain_link(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = next_client.get("/").content.decode()
        anchor = find_anchor(body, text="Peek at columns")
        assert f'href="/board/{board.pk}/"' in anchor
        assert 'data-next-layer="board-peek"' in anchor
        assert body.count(f'href="/board/{board.pk}/"') == 2

    def test_a_plain_get_renders_the_real_board(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert "data-board-peek" not in body
        assert 'id="kanban-board"' in body

    def test_the_zone_get_serves_the_summary_with_its_title(
        self, next_client: NextClient, board: Board
    ) -> None:
        response = next_client.get_zones(f"/board/{board.pk}/", "board-peek")
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph", "meta"]
        html = envelope.html_for_zone("board-peek")
        assert "data-board-peek" in html
        assert f">{board.title}</h2>" in html
        assert html.count("data-peek-lane") == 3
        assert "2 cards" in html
        assert "1 card of 2" in html
        meta = next(op for op in envelope.ops if op["op"] == "meta")
        assert meta["title"] == "Peek · Roadmap · next.dj Kanban"

    def test_a_board_without_columns_says_so(self, next_client: NextClient) -> None:
        empty = Board.objects.create(title="Empty", slug="empty")
        response = next_client.get_zones(f"/board/{empty.pk}/", "board-peek")
        html = envelope_of(response).html_for_zone("board-peek")
        assert "No columns yet." in html

    def test_the_peek_links_to_the_full_board(
        self, next_client: NextClient, board: Board
    ) -> None:
        response = next_client.get_zones(f"/board/{board.pk}/", "board-peek")
        anchor = find_anchor(
            envelope_of(response).html_for_zone("board-peek"), text="Open the board"
        )
        assert f'href="/board/{board.pk}/"' in anchor
        assert "data-next-layer" not in anchor

    def test_an_unknown_board_is_a_404(self, next_client: NextClient) -> None:
        response = next_client.get_zones("/board/9999/", "board-peek")
        assert response.status_code == 404

    def test_the_intercept_is_listed_under_its_target(self) -> None:
        out = StringIO()
        call_command("routes", stdout=out)
        row = next(line for line in out.getvalue().splitlines() if "(.)board" in line)
        assert "/board/<int:board>/" in row
        assert row.rstrip().endswith("board-peek")


def _sign_in(next_client: NextClient, user: User) -> None:
    response = next_client.post_action(
        "sign_in_form", {"username": user.username, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 302


class TestSignIn:
    """Demo members sign in so the board can name who is watching it."""

    def test_the_page_offers_the_form_and_the_demo_accounts(
        self, next_client: NextClient
    ) -> None:
        response = next_client.get("/sign-in/")
        body = response.content.decode()
        assert response.status_code == 200
        assert 'name="password"' in find_form(body, contains="sign-in-username")
        assert "<code>ada</code>" in body
        assert_metadata(body, title="Sign in · next.dj Kanban")

    def test_a_guest_sees_the_sign_in_link(self, next_client: NextClient) -> None:
        body = next_client.get("/").content.decode()
        assert 'href="/sign-in/"' in body
        assert "data-kanban-viewer" not in body

    def test_a_member_signs_in_and_the_header_names_them(
        self, next_client: NextClient, ada: User
    ) -> None:
        response = next_client.post_action(
            "sign_in_form", {"username": "ada", "password": DEMO_PASSWORD}
        )
        assert response.status_code == 302
        assert response["Location"] == "/"
        body = next_client.get("/").content.decode()
        assert "Ada Lovelace" in body
        assert "Sign out" in find_form(body, contains="Sign out")

    def test_a_wrong_password_is_refused(
        self, next_client: NextClient, ada: User
    ) -> None:
        response = next_client.post_action(
            "sign_in_form", {"username": "ada", "password": "nope"}, origin="/sign-in/"
        )
        assert "Unknown username or password." in response.content.decode()
        assert "_auth_user_id" not in next_client.session

    def test_signing_out_ends_the_session(
        self, next_client: NextClient, ada: User
    ) -> None:
        _sign_in(next_client, ada)
        response = next_client.post_action("sign_out_form", {})
        assert response.status_code == 302
        assert "_auth_user_id" not in next_client.session


class TestRealtimeAccess:
    """The board topic, its authorizer and the public member profile."""

    def test_the_topic_names_the_board(self) -> None:
        assert board_topic(7) == "board:7"

    @pytest.mark.parametrize("topic", ["poll:1", "board", "board:", "board:x"], ids=str)
    def test_a_malformed_topic_is_refused(self, topic: str) -> None:
        assert can_watch_board(AnonymousUser(), topic) is False

    def test_an_existing_board_is_granted(self, board: Board) -> None:
        assert can_watch_board(AnonymousUser(), board_topic(board.pk)) is True

    def test_a_deleted_board_is_refused(self, board: Board) -> None:
        topic = board_topic(board.pk)
        board.delete()
        assert can_watch_board(AnonymousUser(), topic) is False

    def test_a_member_is_listed_by_name_initials_and_a_subject_key(
        self, ada: User
    ) -> None:
        assert identify_member(ada, "board:1", SUBJECT) == {
            "name": "Ada Lovelace",
            "initials": "AL",
            "key": SUBJECT[:12],
        }

    def test_two_members_sharing_a_name_stay_apart(self, ada: User) -> None:
        first = identify_member(ada, "board:1", SUBJECT)
        second = identify_member(ada, "board:1", OTHER_SUBJECT)
        assert first["name"] == second["name"]
        assert first != second

    def test_a_member_without_a_full_name_falls_back_to_the_username(self, db) -> None:
        user = User.objects.create_user("linus")
        assert public_profile(user) == {"name": "linus", "initials": "L"}

    def test_a_guest_is_named_by_a_short_tag_of_its_subject(self) -> None:
        assert identify_member(AnonymousUser(), "board:1", SUBJECT) == {
            "name": "Guest f00d",
            "initials": "G",
            "key": SUBJECT[:12],
        }

    def test_a_guest_without_a_subject_is_counted_unnamed(self) -> None:
        assert identify_member(AnonymousUser(), "board:1", "") is None


class TestBoardStream:
    """The board page subscribes to its topic through the stream page."""

    def test_the_board_page_names_the_stream_and_wraps_the_island_in_a_zone(
        self, next_client: NextClient, board: Board
    ) -> None:
        body = _board_html(next_client, board)
        assert f'data-next-stream="/board/{board.pk}/stream/"' in body
        assert '<div data-next-zone="board">' in body
        assert '<div id="kanban-board" data-next-keep' in body
        assert init_payload(body)["board"]["topic"] == f"board:{board.pk}"

    def test_the_stream_lists_a_signed_in_watcher(
        self, next_client: NextClient, board: Board, ada: User
    ) -> None:
        _sign_in(next_client, ada)
        topic = board_topic(board.pk)
        with memory_broker():
            response = next_client.get(f"/board/{board.pk}/stream/")
            with SseReader(response) as events:
                assert next(events).event == "next-hello"
                attendance = presence(topic)
        (member,) = attendance.members
        assert attendance.count == 1
        assert (member["name"], member["initials"]) == ("Ada Lovelace", "AL")
        assert len(member["key"]) == 12

    def test_the_stream_lists_a_guest_under_a_short_tag(
        self, next_client: NextClient, board: Board
    ) -> None:
        next_client.session.save()
        with memory_broker():
            response = next_client.get(f"/board/{board.pk}/stream/")
            with SseReader(response) as events:
                assert next(events).event == "next-hello"
                attendance = presence(board_topic(board.pk))
        (guest,) = attendance.members
        assert guest["name"].startswith("Guest ")
        assert len(guest["name"]) == len("Guest ") + 4
        assert guest["key"].startswith(guest["name"].removeprefix("Guest "))
        assert len(guest["key"]) == 12

    def test_an_unknown_board_has_no_stream(self, next_client: NextClient) -> None:
        assert next_client.get("/board/9999/stream/").status_code == 404


class TestBoardBroadcast:
    """A committed move or create asks the other tabs to refresh the board zone."""

    def test_a_partial_move_answers_with_the_board_it_now_reads(
        self,
        next_client: NextClient,
        board: Board,
        backlog: Column,
        done: Column,
        django_capture_on_commit_callbacks,
    ) -> None:
        card = backlog.cards.get(title="Plan")
        with (
            memory_broker(),
            capture_published(board_topic(board.pk)) as published,
            django_capture_on_commit_callbacks(execute=True),
        ):
            response = next_client.post_action(
                "move_card_form",
                {"card_id": card.pk, "target_column": done.pk, "target_position": "0"},
                origin=f"/board/{board.pk}/",
                partial=True,
                zones="board",
                headers={"X-Next-Request-Id": "move-1"},
            )
        envelope = envelope_of(response)
        ops = envelope.ops
        assert [op["op"] for op in ops] == ["context"]
        columns = ops[0]["data"]["board"]["columns"]
        landed = next(col for col in columns if col["id"] == done.pk)
        assert [c["title"] for c in landed["cards"]] == ["Plan", "Ship"]
        (message,) = published.messages
        assert message.envelope.request_id == "move-1"
        assert [op.op for op in message.envelope.ops] == ["refresh"]

    def test_a_rejected_partial_move_morphs_the_board_zone_and_publishes_nothing(
        self, next_client: NextClient, board: Board, done: Column
    ) -> None:
        with memory_broker(), capture_published() as published:
            response = next_client.post_action(
                "move_card_form",
                {"card_id": "9999", "target_column": done.pk, "target_position": "0"},
                origin=f"/board/{board.pk}/",
                partial=True,
                zones="board",
            )
        assert response["X-Next-Form"] == "invalid"
        morph, board_context = envelope_of(response).ops
        assert morph["target"] == {"zone": "board"}
        assert board_context["op"] == "context"
        assert board_context["data"]["board"]["id"] == board.pk
        assert len(published) == 0

    def test_a_created_card_refreshes_the_board(
        self,
        next_client: NextClient,
        board: Board,
        backlog: Column,
        django_capture_on_commit_callbacks,
    ) -> None:
        with (
            memory_broker(),
            capture_published(board_topic(board.pk)) as published,
            django_capture_on_commit_callbacks(execute=True),
        ):
            next_client.post_action(
                "create_card_form", {"column": backlog.pk, "title": "Fresh"}
            )
        assert len(published) == 1

    def test_a_card_over_the_limit_publishes_nothing(
        self, next_client: NextClient, progress: Column
    ) -> None:
        Card.objects.create(column=progress, title="Fill", position=1)
        with (
            patch("kanban.forms._is_full", side_effect=[False, True]),
            memory_broker(),
            capture_published() as published,
        ):
            response = next_client.post_action(
                "create_card_form", {"column": progress.pk, "title": "Late"}
            )
        assert response.status_code == 400
        assert len(published) == 0

    def test_other_actions_publish_nothing(
        self, next_client: NextClient, board: Board
    ) -> None:
        with memory_broker(), capture_published() as published:
            next_client.post_action(
                "rename_board_form",
                {"title": "Renamed"},
                origin=f"/board/{board.pk}/settings/",
            )
        assert len(published) == 0


def _upstream(board: Board, data: object, user: object, subject: str = "") -> Upstream:
    return Upstream(
        topic=board_topic(board.pk),
        name="dragging",
        data=data,
        user=user,
        subject=subject,
        connection="c1",
        params={"id": str(board.pk)},
    )


class TestDraggingHandler:
    """A picked-up card is announced to the board with who holds it."""

    def test_a_drag_start_names_the_member(
        self, board: Board, backlog: Column, ada: User
    ) -> None:
        card = backlog.cards.first()
        with memory_broker(), capture_published() as published:
            dragging(_upstream(board, {"card": card.pk, "active": True}, ada))
        (message,) = published.messages
        (op,) = message.envelope.as_dict()["ops"]
        assert op["name"] == "board:dragging"
        assert op["detail"] == {
            "card": card.pk,
            "active": True,
            "by": {"name": "Ada Lovelace", "initials": "AL", "key": ""},
            "from": "c1",
        }

    def test_a_guest_drag_names_the_guest_presence_lists(
        self, board: Board, backlog: Column
    ) -> None:
        card = backlog.cards.first()
        upstream = _upstream(
            board, {"card": card.pk, "active": True}, AnonymousUser(), "beefcafe"
        )
        with memory_broker(), capture_published() as published:
            dragging(upstream)
        detail = published.messages[0].envelope.as_dict()["ops"][0]["detail"]
        assert detail["by"] == {
            "name": "Guest beef",
            "initials": "G",
            "key": "beefcafe",
        }

    def test_a_guest_without_a_subject_drags_as_a_guest(
        self, board: Board, backlog: Column
    ) -> None:
        card = backlog.cards.first()
        with memory_broker(), capture_published() as published:
            dragging(
                _upstream(board, {"card": card.pk, "active": True}, AnonymousUser())
            )
        detail = published.messages[0].envelope.as_dict()["ops"][0]["detail"]
        assert detail["by"] == GUEST

    @pytest.mark.parametrize(
        "data",
        [None, {"card": "1", "active": True}, {"card": 1, "active": 1}, {"card": True}],
        ids=["no-object", "string-card", "int-active", "bool-card"],
    )
    def test_malformed_data_is_dropped(self, board: Board, data: object) -> None:
        with memory_broker(), capture_published() as published:
            dragging(_upstream(board, data, AnonymousUser()))
        assert len(published) == 0

    def test_a_card_of_another_board_is_dropped(self, board: Board) -> None:
        other = Board.objects.create(title="Other", slug="other")
        column = Column.objects.create(board=other, title="X", position=0)
        card = Card.objects.create(column=column, title="Elsewhere", position=0)
        with memory_broker(), capture_published() as published:
            dragging(_upstream(board, {"card": card.pk, "active": True}, None))
        assert len(published) == 0


@pytest.mark.django_db(transaction=True)
def test_a_dragging_event_over_the_socket_reaches_the_board(demo_data: None) -> None:
    board = Board.objects.get(slug="engineering-roadmap")
    card = Card.objects.filter(column__board=board).first()
    topic = board_topic(board.pk)
    ticket = issue_socket_ticket(
        (topic,), authorizer_ref="kanban.realtime.can_watch_board"
    )
    headers = [(b"origin", b"http://testserver"), (b"host", b"testserver")]
    headers.append(ticket.cookie)

    async def scenario() -> tuple[object, str]:
        app = realtime.application(get_asgi_application())
        communicator = WebSocketCommunicator(app, "/_next/ws/", headers, timeout=5)
        connected, _ = await communicator.connect()
        assert connected
        hello = await communicator.subscribe(ticket)
        ack = await communicator.send_up(
            topic, "dragging", {"card": card.pk, "active": True}
        )
        await communicator.disconnect()
        return ack, hello["connection"]

    with (
        override_websocket_mount(),
        memory_broker(),
        capture_published(topic) as published,
    ):
        ack, connection = asyncio.run(scenario())
    assert ack is None
    (message,) = published.messages
    detail = message.envelope.as_dict()["ops"][0]["detail"]
    assert detail["from"] == connection
    assert detail["by"]["name"].startswith("Guest ")
