import json
import re

import pytest
from django.test import override_settings
from django.urls import reverse
from kanban.models import Board, Card, Column

from next.testing import NextClient


pytestmark = pytest.mark.django_db


@pytest.fixture()
def board(db) -> Board:
    """Build a small board with three columns and four cards for e2e flows."""
    del db
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
    del db
    return Board.objects.create(title="Archived", slug="archived", archived=True)


def _board_html(client: NextClient, board: Board) -> str:
    response = client.get(f"/board/{board.pk}/")
    assert response.status_code == 200
    return response.content.decode()


def _settings_html(client: NextClient, board: Board) -> str:
    response = client.get(f"/board/{board.pk}/settings/")
    assert response.status_code == 200
    return response.content.decode()


def _next_init_payload(html: str) -> dict:
    match = re.search(r"Next\._init\((\{.*?\})\)", html)
    assert match is not None, "Next._init call missing"
    return json.loads(match.group(1))


class TestBoardList:
    """The index page lists active boards and hides archived ones."""

    def test_renders_active_boards(
        self,
        client: NextClient,
        board: Board,
        archived_board: Board,
    ) -> None:
        del archived_board
        response = client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert board.title in body

    def test_archived_hidden(
        self,
        client: NextClient,
        archived_board: Board,
    ) -> None:
        response = client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert f'href="/board/{archived_board.pk}/"' not in body

    def test_empty_state(self, client: NextClient) -> None:
        Board.objects.all().delete()
        response = client.get("/")
        body = response.content.decode()
        assert response.status_code == 200
        assert "No active boards" in body


class TestBoardView:
    """The board detail page renders columns, cards, and React CDN."""

    def test_columns_and_cards_render(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        assert "Backlog" in body
        assert "In Progress" in body
        assert "Done" in body
        assert "Plan" in body
        assert "Ship" in body

    def test_nested_layout_chain(self, client: NextClient, board: Board) -> None:
        body = _board_html(client, board)
        assert "🗂️ next.dj Kanban" in body  # root layout marker
        assert "Board #" in body  # nested board layout marker
        assert "data-kanban-root" in body  # template marker

    def test_react_cdn_links_present(self, client: NextClient, board: Board) -> None:
        body = _board_html(client, board)
        assert "react.production.min.js" in body
        assert "react-dom.production.min.js" in body
        assert "babel.min.js" in body

    def test_inherit_context_visible_in_settings(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _settings_html(client, board)
        assert board.title in body


class TestSettings:
    """Settings page renders three forms and each one mutates state."""

    def test_get_renders_forms(self, client: NextClient, board: Board) -> None:
        body = _settings_html(client, board)
        assert "Rename board" in body
        assert "Add column" in body
        assert "Archive" in body

    def test_rename_form_post_redirects(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        response = client.post_action(
            "kanban:rename_board",
            {"board_id": board.pk, "title": "New title"},
        )
        assert response.status_code == 302
        board.refresh_from_db()
        assert board.title == "New title"

    def test_archive_form_toggles(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        response = client.post_action(
            "kanban:archive_board",
            {"board_id": board.pk, "archived": "1"},
        )
        assert response.status_code == 302
        board.refresh_from_db()
        assert board.archived is True

    def test_archive_unset_redirects_back_to_settings(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        response = client.post_action(
            "kanban:archive_board",
            {"board_id": board.pk},
        )
        assert response.status_code == 302
        assert response["Location"] == f"/board/{board.pk}/settings/"

    def test_add_column_form_post(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        before = board.columns.count()
        response = client.post_action(
            "kanban:create_column",
            {"board_id": board.pk, "title": "New", "wip_limit": "5"},
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
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        done = board.columns.get(title="Done")
        card = backlog.cards.first()
        response = client.post_action(
            "kanban:move_card",
            {
                "card_id": card.pk,
                "target_column_id": done.pk,
                "target_position": "0",
            },
        )
        assert response.status_code == 302
        card.refresh_from_db()
        assert card.column_id == done.pk

    def test_post_normalises_positions(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        done = board.columns.get(title="Done")
        card = backlog.cards.first()
        client.post_action(
            "kanban:move_card",
            {
                "card_id": card.pk,
                "target_column_id": done.pk,
                "target_position": "0",
            },
        )
        positions = list(
            done.cards.order_by("position").values_list("position", flat=True)
        )
        assert positions == list(range(len(positions)))

    def test_cross_board_target_rejected(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        other_board = Board.objects.create(title="Other", slug="other")
        other_col = Column.objects.create(board=other_board, title="X", position=0)
        card = board.columns.first().cards.first()
        response = client.post_action(
            "kanban:move_card",
            {
                "card_id": card.pk,
                "target_column_id": other_col.pk,
                "target_position": "0",
            },
        )
        assert response.status_code == 400
        card.refresh_from_db()
        assert card.column.board_id == board.pk

    def test_negative_position_rejected(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        done = board.columns.get(title="Done")
        card = backlog.cards.first()
        response = client.post_action(
            "kanban:move_card",
            {
                "card_id": card.pk,
                "target_column_id": done.pk,
                "target_position": "-1",
            },
        )
        assert response.status_code == 400


class TestCardExcerpt:
    """The card component truncates long bodies for in-card preview."""

    def test_long_body_is_truncated_with_ellipsis(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        Card.objects.create(
            column=backlog,
            title="Long card",
            body="x" * 200,
            position=99,
        )
        body = _board_html(client, board)
        assert "…" in body


class TestPreviewComponent:
    """The preview composite renders after a successful move."""

    def test_preview_appears_with_moved_query(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        done = board.columns.get(title="Done")
        card = backlog.cards.first()
        client.post_action(
            "kanban:move_card",
            {
                "card_id": card.pk,
                "target_column_id": done.pk,
                "target_position": "0",
            },
        )
        response = client.get(f"/board/{board.pk}/?moved={card.pk}")
        body = response.content.decode()
        assert "Move complete" in body
        assert f'data-kanban-preview="{card.pk}"' in body

    def test_preview_hidden_without_moved_query(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        assert "Move complete" not in body

    def test_preview_skips_unknown_card_id(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        response = client.get(f"/board/{board.pk}/?moved=99999")
        body = response.content.decode()
        assert "Move complete" not in body


class TestCreateCard:
    """The create_card form action appends cards and respects WIP limits."""

    def test_post_appends_card_at_tail(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        before = backlog.cards.count()
        response = client.post_action(
            "kanban:create_card",
            {"column_id": backlog.pk, "title": "Extra"},
        )
        assert response.status_code == 302
        backlog.refresh_from_db()
        assert backlog.cards.count() == before + 1
        new = backlog.cards.order_by("-position").first()
        assert new.title == "Extra"
        assert new.position == before

    def test_wip_limit_blocks_creation(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        progress = board.columns.get(title="In Progress")
        Card.objects.create(column=progress, title="Second", position=1)
        response = client.post_action(
            "kanban:create_card",
            {"column_id": progress.pk, "title": "Over the limit"},
        )
        assert response.status_code == 400
        assert progress.cards.filter(title="Over the limit").count() == 0

    def test_wip_limit_none_allows_unlimited(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        backlog = board.columns.get(title="Backlog")
        for index in range(5):
            response = client.post_action(
                "kanban:create_card",
                {"column_id": backlog.pk, "title": f"Card {index}"},
            )
            assert response.status_code == 302
        assert backlog.cards.count() >= 5


class TestCssDedup:
    """Identical bytes in column.css and card.css collapse to one link tag."""

    def test_identical_column_card_css_collapses_to_one_link(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        kanban_links = re.findall(r'href="/static/next/components/[^"]+\.css"', body)
        # column/component.css and card/component.css ship byte-for-byte
        # identical content. HashContentDedup hashes the bytes, so the
        # collector keeps only the first registration, whichever wins.
        assert len(kanban_links) == 1

    def test_dedup_picks_one_winning_url(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        link_match = re.search(r'href="(/static/next/components/[^"]+\.css)"', body)
        assert link_match is not None
        url = link_match.group(1)
        assert url.endswith(("column.css", "card.css"))


class TestJsxBackend:
    """The kanban backend renders .jsx URLs as <script type="text/babel">."""

    def test_column_jsx_block_rendered(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        assert re.search(
            r'<script type="text/babel" src="[^"]*column\.jsx"></script>',
            body,
        )

    def test_card_jsx_block_rendered(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        assert re.search(
            r'<script type="text/babel" src="[^"]*card\.jsx"></script>',
            body,
        )

    def test_render_link_tag_unaffected(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        assert '<link rel="stylesheet"' in body


class TestJsContext:
    """`Next._init({...})` carries deep-merged board state."""

    def test_next_init_call_contains_board(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        payload = _next_init_payload(body)
        assert payload["board"]["id"] == board.pk
        assert payload["board"]["title"] == board.title
        assert "csrf" in payload["board"]

    def test_deep_merge_columns_present(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        payload = _next_init_payload(body)
        cols = payload["board"]["columns"]
        assert {c["title"] for c in cols} >= {"Backlog", "In Progress", "Done"}
        backlog = next(c for c in cols if c["title"] == "Backlog")
        assert {card["title"] for card in backlog["cards"]} == {"Plan", "Spec"}

    def test_first_wins_policy_loses_data(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "APP_DIRS": True,
                        "DIRS": [],
                        "PAGES_DIR": "boards",
                        "OPTIONS": {"context_processors": []},
                    }
                ],
                "DEFAULT_COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_pieces",
                    }
                ],
                "DEFAULT_STATIC_BACKENDS": [
                    {
                        "BACKEND": "kanban.backends.BabelStaticBackend",
                        "OPTIONS": {
                            "DEDUP_STRATEGY": (
                                "kanban.static_policies.HashContentDedup"
                            ),
                            "JS_CONTEXT_POLICY": (
                                "next.static.collector.FirstWinsPolicy"
                            ),
                        },
                    }
                ],
            }
        ):
            body = _board_html(client, board)
            payload = _next_init_payload(body)
            board_payload = payload["board"]
            # FirstWinsPolicy keeps whichever key is registered first.
            # The page-level context registers (id, title, slug, archived,
            # csrf), while the component-level registers `columns`. With
            # FirstWins they cannot both survive — at least one of the two
            # sets is missing.
            has_page_keys = "id" in board_payload and "csrf" in board_payload
            has_columns = "columns" in board_payload
            assert not (has_page_keys and has_columns)


class TestInheritedHeaderCount:
    """`active_boards_count` flows from `boards/page.py` into board detail."""

    def test_index_shows_count(self, client: NextClient, board: Board) -> None:
        del board
        response = client.get("/")
        body = response.content.decode()
        match = re.search(r"(\d+) active boards", body)
        assert match is not None
        assert int(match.group(1)) >= 1

    def test_board_detail_inherits_count(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        assert "active boards" in body

    def test_settings_inherits_count(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _settings_html(client, board)
        assert "active boards" in body


class TestCdnCachePolicy:
    """External CDN scripts render with cache-friendly attributes."""

    @pytest.mark.parametrize(
        "needle",
        [
            "react.production.min.js",
            "react-dom.production.min.js",
            "babel.min.js",
        ],
    )
    def test_cdn_script_has_crossorigin(
        self,
        client: NextClient,
        board: Board,
        needle: str,
    ) -> None:
        body = _board_html(client, board)
        match = re.search(
            rf'<script[^>]*src="[^"]*{re.escape(needle)}"[^>]*></script>',
            body,
        )
        assert match is not None
        tag = match.group(0)
        assert 'crossorigin="anonymous"' in tag
        assert 'referrerpolicy="no-referrer"' in tag

    def test_local_script_has_no_cache_attrs(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        del board
        response = client.get("/")
        body = response.content.decode()
        match = re.search(
            r'<script src="/static/next/next\.min\.js"[^>]*></script>', body
        )
        assert match is not None
        assert "crossorigin" not in match.group(0)


class TestReactCdn:
    """React, react-dom, and babel CDN tags appear before any text/babel block."""

    @pytest.mark.parametrize(
        "needle",
        [
            "react.production.min.js",
            "react-dom.production.min.js",
            "babel.min.js",
        ],
    )
    def test_cdn_script_present(
        self,
        client: NextClient,
        board: Board,
        needle: str,
    ) -> None:
        body = _board_html(client, board)
        assert needle in body

    def test_cdn_scripts_in_correct_order_before_babel_blocks(
        self,
        client: NextClient,
        board: Board,
    ) -> None:
        body = _board_html(client, board)
        cdn_indices = [
            body.index("react.production.min.js"),
            body.index("react-dom.production.min.js"),
            body.index("babel.min.js"),
        ]
        babel_block = body.index('<script type="text/babel"')
        assert all(idx < babel_block for idx in cdn_indices)
        assert cdn_indices == sorted(cdn_indices)


def test_index_page_has_module_help(client: NextClient) -> None:
    """The default response uses the next.dj page reverse helper."""
    url = reverse("next:page_")
    assert url == "/"
