import inspect

import pytest
from django.http import Http404, HttpRequest
from kanban.backends import BabelStaticBackend
from kanban.forms import CreateCardForm, MoveCardForm
from kanban.models import Board, Card, Column
from kanban.providers import BoardProvider, CardProvider, DBoard, DCard
from kanban.static_policies import DeepMergePolicy, HashContentDedup

from next.deps.cache import DependencyCache
from next.deps.context import ResolutionContext
from next.static.collector import (
    DeepMergePolicy as CoreDeepMergePolicy,
    HashContentDedup as CoreHashContentDedup,
)


pytestmark = pytest.mark.django_db


@pytest.fixture()
def board() -> Board:
    return Board.objects.create(title="Test", slug="test")


@pytest.fixture()
def two_columns(board: Board) -> tuple[Column, Column]:
    a = Column.objects.create(board=board, title="A", position=0)
    b = Column.objects.create(board=board, title="B", position=1)
    return a, b


class TestModelStrings:
    def test_board_str(self, board: Board) -> None:
        assert str(board) == "Test"

    def test_column_str(self, two_columns: tuple[Column, Column]) -> None:
        a, _ = two_columns
        assert str(a) == "A"

    def test_card_str(self, two_columns: tuple[Column, Column]) -> None:
        a, _ = two_columns
        card = Card.objects.create(column=a, title="Hello", position=0)
        assert str(card) == "Hello"


class TestStaticPoliciesReexports:
    def test_hash_dedup_subclasses_core(self) -> None:
        assert isinstance(HashContentDedup(), CoreHashContentDedup)

    def test_deep_merge_subclasses_core(self) -> None:
        assert isinstance(DeepMergePolicy(), CoreDeepMergePolicy)


class TestBabelStaticBackend:
    def test_jsx_local_url_renders_without_cache_attrs(self) -> None:
        backend = BabelStaticBackend()
        out = backend.render_babel_script_tag("/static/next/components/column.jsx")
        assert out == (
            '<script type="text/babel" '
            'src="/static/next/components/column.jsx"></script>'
        )

    def test_external_jsx_url_carries_cache_attrs(self) -> None:
        backend = BabelStaticBackend()
        out = backend.render_babel_script_tag("https://cdn.example.com/lib.jsx")
        assert out == (
            '<script type="text/babel" src="https://cdn.example.com/lib.jsx" '
            'crossorigin="anonymous" referrerpolicy="no-referrer"></script>'
        )

    def test_external_script_url_carries_cache_attrs(self) -> None:
        backend = BabelStaticBackend()
        out = backend.render_script_tag("https://unpkg.com/react/umd/react.min.js")
        assert 'crossorigin="anonymous"' in out
        assert 'referrerpolicy="no-referrer"' in out
        assert 'src="https://unpkg.com/react/umd/react.min.js"' in out

    def test_local_script_url_unaffected(self) -> None:
        backend = BabelStaticBackend()
        out = backend.render_script_tag("/static/next/components/column.js")
        assert out == '<script src="/static/next/components/column.js"></script>'

    def test_render_link_tag_unaffected(self) -> None:
        backend = BabelStaticBackend()
        out = backend.render_link_tag("/static/next/components/column.css")
        assert out == (
            '<link rel="stylesheet" href="/static/next/components/column.css">'
        )

    def test_custom_cache_attrs_override_defaults(self) -> None:
        backend = BabelStaticBackend(
            {"OPTIONS": {"SCRIPT_CACHE_ATTRS": {"integrity": "sha256-x"}}}
        )
        out = backend.render_script_tag("https://cdn.example.com/x.js")
        assert 'integrity="sha256-x"' in out
        assert "crossorigin" not in out


class TestBoardProvider:
    def test_can_handle_matches_dboard_subscript(self) -> None:
        provider = BoardProvider()

        def func(b: DBoard[Board]) -> None: ...

        param = inspect.signature(func).parameters["b"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        assert provider.can_handle(param, ctx)

    def test_resolve_returns_board(self, board: Board) -> None:
        provider = BoardProvider()

        def func(b: DBoard[Board]) -> None: ...

        param = inspect.signature(func).parameters["b"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={"id": board.pk},
            context_data={},
            cache=DependencyCache(),
        )
        assert provider.resolve(param, ctx) == board

    def test_resolve_returns_none_without_id(self) -> None:
        provider = BoardProvider()

        def func(b: DBoard[Board]) -> None: ...

        param = inspect.signature(func).parameters["b"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        assert provider.resolve(param, ctx) is None

    def test_resolve_raises_404_for_missing_id(self, board: Board) -> None:
        del board
        provider = BoardProvider()

        def func(b: DBoard[Board]) -> None: ...

        param = inspect.signature(func).parameters["b"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={"id": 99999},
            context_data={},
            cache=DependencyCache(),
        )
        with pytest.raises(Http404):
            provider.resolve(param, ctx)


class TestCardProvider:
    def test_can_handle_requires_post_card_id(
        self, two_columns: tuple[Column, Column]
    ) -> None:
        del two_columns
        provider = CardProvider()

        def func(c: DCard[Card]) -> None: ...

        param = inspect.signature(func).parameters["c"]
        request = HttpRequest()
        request.method = "POST"
        ctx = ResolutionContext(
            request=request,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        assert not provider.can_handle(param, ctx)

    def test_can_handle_without_request(self) -> None:
        provider = CardProvider()

        def func(c: DCard[Card]) -> None: ...

        param = inspect.signature(func).parameters["c"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        assert not provider.can_handle(param, ctx)

    def test_can_handle_skips_non_dcard_annotation(self) -> None:
        provider = CardProvider()

        def func(c: int) -> None: ...

        param = inspect.signature(func).parameters["c"]
        ctx = ResolutionContext(
            request=None,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        assert not provider.can_handle(param, ctx)

    def test_resolve_returns_card_for_post_id(
        self,
        two_columns: tuple[Column, Column],
    ) -> None:
        col_a, _ = two_columns
        card = Card.objects.create(column=col_a, title="Move me", position=0)
        provider = CardProvider()

        def func(c: DCard[Card]) -> None: ...

        param = inspect.signature(func).parameters["c"]
        request = HttpRequest()
        request.method = "POST"
        request.POST = {"card_id": str(card.pk)}  # type: ignore[assignment]
        ctx = ResolutionContext(
            request=request,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        assert provider.can_handle(param, ctx)
        assert provider.resolve(param, ctx) == card

    def test_resolve_raises_404_when_missing(self) -> None:
        provider = CardProvider()

        def func(c: DCard[Card]) -> None: ...

        param = inspect.signature(func).parameters["c"]
        request = HttpRequest()
        request.method = "POST"
        request.POST = {"card_id": "99999"}  # type: ignore[assignment]
        ctx = ResolutionContext(
            request=request,
            form=None,
            url_kwargs={},
            context_data={},
            cache=DependencyCache(),
        )
        with pytest.raises(Http404):
            provider.resolve(param, ctx)


class TestMoveCardFormClean:
    def test_cross_board_move_rejected(
        self,
        two_columns: tuple[Column, Column],
    ) -> None:
        col_a, _ = two_columns
        other_board = Board.objects.create(title="Other", slug="other")
        other_col = Column.objects.create(board=other_board, title="X", position=0)
        card = Card.objects.create(column=col_a, title="Stay", position=0)

        form = MoveCardForm(
            data={
                "card_id": str(card.pk),
                "target_column_id": str(other_col.pk),
                "target_position": "0",
            },
        )
        assert not form.is_valid()
        assert "across boards" in str(form.errors)

    def test_unknown_card_rejected(self) -> None:
        form = MoveCardForm(
            data={
                "card_id": "99999",
                "target_column_id": "99999",
                "target_position": "0",
            },
        )
        assert not form.is_valid()
        assert "Unknown card" in str(form.errors)


class TestCreateCardFormClean:
    def test_wip_limit_blocks_creation(
        self,
        two_columns: tuple[Column, Column],
    ) -> None:
        col_a, _ = two_columns
        col_a.wip_limit = 1
        col_a.save()
        Card.objects.create(column=col_a, title="One", position=0)

        form = CreateCardForm(
            data={"column_id": str(col_a.pk), "title": "Two"},
        )
        assert not form.is_valid()
        assert "WIP limit" in str(form.errors)

    def test_unknown_column_rejected(self) -> None:
        form = CreateCardForm(
            data={"column_id": "99999", "title": "Lost"},
        )
        assert not form.is_valid()
        assert "Unknown column" in str(form.errors)

    def test_blank_form_short_circuits_clean(self) -> None:
        form = CreateCardForm(data={"column_id": "", "title": ""})
        assert not form.is_valid()
        assert "column_id" in form.errors


class TestMoveCardFormBlankCleanedReturns:
    """Field-level errors keep the cross-form clean from running database lookups."""

    def test_missing_fields_skip_database_lookups(self) -> None:
        form = MoveCardForm(data={"card_id": "", "target_column_id": ""})
        assert not form.is_valid()
        assert "card_id" in form.errors
