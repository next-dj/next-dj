import base64
import importlib.util
import inspect
import json
from pathlib import Path

import pytest
from django.http import Http404, HttpRequest
from kanban.backends import ViteManifestBackend
from kanban.forms import CreateCardForm, MoveCardForm
from kanban.models import Board, Card, Column
from kanban.providers import BoardProvider, CardProvider, DBoard, DCard
from kanban.signals import inject_vite_dev_assets

from next.deps.cache import DependencyCache
from next.deps.context import ResolutionContext
from next.static.assets import StaticAsset
from next.static.collector import StaticCollector


pytestmark = pytest.mark.django_db

_PIECES = (
    Path(__file__).parent.parent
    / "kanban"
    / "boards"
    / "board"
    / "[int:id]"
    / "_pieces"
)


def _load(relative: str):
    spec = importlib.util.spec_from_file_location(
        f"_test_{relative.replace('/', '_').replace('.py', '')}",
        _PIECES / relative,
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


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


class TestCardExcerptProperty:
    def test_short_body_returned_verbatim(
        self, two_columns: tuple[Column, Column]
    ) -> None:
        col_a, _ = two_columns
        card = Card.objects.create(column=col_a, title="T", body="Short", position=0)
        assert card.excerpt == "Short"

    def test_empty_body_returns_empty_string(
        self, two_columns: tuple[Column, Column]
    ) -> None:
        col_a, _ = two_columns
        card = Card.objects.create(column=col_a, title="T", body="", position=0)
        assert card.excerpt == ""

    def test_long_body_truncated_with_ellipsis(
        self, two_columns: tuple[Column, Column]
    ) -> None:
        col_a, _ = two_columns
        long_body = "x" * 200
        card = Card.objects.create(column=col_a, title="T", body=long_body, position=0)
        result = card.excerpt
        assert result.endswith("…")
        assert len(result) <= 100


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

    def test_missing_fields_skip_database_lookups(self) -> None:
        form = MoveCardForm(data={"card_id": "", "target_column_id": ""})
        assert not form.is_valid()
        assert "card_id" in form.errors


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


class TestViteManifestBackendRegisterFile:
    def test_non_jsx_delegates_to_super(self) -> None:
        backend = ViteManifestBackend()
        out = backend.register_file(Path("/tmp/style.css"), "kanban/style.css", "css")
        assert "kanban/style.css" in out

    def test_jsx_no_dev_no_manifest_delegates_to_super(self) -> None:
        backend = ViteManifestBackend()
        out = backend.register_file(Path("/tmp/page.jsx"), "kanban/page.jsx", "jsx")
        assert "kanban/page.jsx" in out

    def test_jsx_with_manifest_resolves_hashed_url(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"page.jsx": {"file": "assets/page-abc.js"}}))
        backend = ViteManifestBackend(
            {"OPTIONS": {"MANIFEST_PATH": str(manifest), "VITE_ROOT": str(tmp_path)}}
        )
        jsx = tmp_path / "page.jsx"
        out = backend.register_file(jsx, "page.jsx", "jsx")
        assert "page-abc.js" in out

    def test_jsx_with_manifest_missing_entry_falls_back(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text("{}")
        backend = ViteManifestBackend(
            {"OPTIONS": {"MANIFEST_PATH": str(manifest), "VITE_ROOT": str(tmp_path)}}
        )
        jsx = tmp_path / "unknown.jsx"
        out = backend.register_file(jsx, "unknown.jsx", "jsx")
        assert isinstance(out, str)

    def test_jsx_dev_origin_returns_dev_url(self) -> None:
        backend = ViteManifestBackend(
            {"OPTIONS": {"DEV_ORIGIN": "http://localhost:5173"}}
        )
        out = backend.register_file(
            Path("/some/path/page.jsx"), "kanban/page.jsx", "jsx"
        )
        assert out == "http://localhost:5173/page.jsx"

    def test_jsx_with_missing_manifest_file_falls_back(
        self,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        manifest = tmp_path / "missing.json"  # never created
        backend = ViteManifestBackend(
            {"OPTIONS": {"MANIFEST_PATH": str(manifest), "VITE_ROOT": str(tmp_path)}}
        )
        jsx = tmp_path / "page.jsx"
        with caplog.at_level("WARNING"):
            out = backend.register_file(jsx, "page.jsx", "jsx")
        assert isinstance(out, str)
        assert any("manifest not found" in r.message for r in caplog.records)
        # Repeat call: warning emitted only once.
        caplog.clear()
        with caplog.at_level("WARNING"):
            backend.register_file(jsx, "page.jsx", "jsx")
        assert not any("manifest not found" in r.message for r in caplog.records)


class TestViteManifestBackendBuildDevUrl:
    def test_path_outside_vite_root_falls_back_to_name(self) -> None:
        backend = ViteManifestBackend(
            {"OPTIONS": {"DEV_ORIGIN": "http://localhost:5173", "VITE_ROOT": "/other"}}
        )
        out = backend._build_dev_url(Path("/completely/different/component.jsx"))
        assert out == "http://localhost:5173/component.jsx"

    def test_no_vite_root_uses_filename(self) -> None:
        backend = ViteManifestBackend(
            {"OPTIONS": {"DEV_ORIGIN": "http://localhost:5173"}}
        )
        out = backend._build_dev_url(Path("/any/path/page.jsx"))
        assert out == "http://localhost:5173/page.jsx"


class TestViteManifestBackendManifestKey:
    def test_with_vite_root_returns_relative_path(self, tmp_path: Path) -> None:
        jsx = tmp_path / "kanban" / "page.jsx"
        backend = ViteManifestBackend({"OPTIONS": {"VITE_ROOT": str(tmp_path)}})
        assert backend._manifest_key(jsx) == "kanban/page.jsx"

    def test_path_outside_vite_root_falls_back_to_name(self) -> None:
        backend = ViteManifestBackend({"OPTIONS": {"VITE_ROOT": "/other/root"}})
        assert (
            backend._manifest_key(Path("/different/component.jsx")) == "component.jsx"
        )

    def test_without_vite_root_returns_filename(self) -> None:
        backend = ViteManifestBackend()
        assert backend._manifest_key(Path("/any/path/component.jsx")) == "component.jsx"


class TestViteManifestBackendLoadManifest:
    def test_parses_json(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"page.jsx": {"file": "assets/page-abc.js"}}')
        backend = ViteManifestBackend({"OPTIONS": {"MANIFEST_PATH": str(manifest)}})
        assert backend._load_manifest() == {"page.jsx": {"file": "assets/page-abc.js"}}

    def test_caches_result(self, tmp_path: Path) -> None:
        manifest = tmp_path / "manifest.json"
        manifest.write_text('{"a": {"file": "b.js"}}')
        backend = ViteManifestBackend({"OPTIONS": {"MANIFEST_PATH": str(manifest)}})
        first = backend._load_manifest()
        manifest.write_text('{"changed": true}')
        assert backend._load_manifest() is first


def _decode_preamble_data_url(url: str) -> str:
    prefix = "data:text/javascript;base64,"
    assert url.startswith(prefix)
    return base64.b64decode(url[len(prefix) :]).decode()


class TestInjectViteDevAssetsGuard:
    def test_skips_when_no_jsx_assets(self) -> None:
        collector = StaticCollector()
        inject_vite_dev_assets(collector)
        assert collector.assets_in_slot("scripts") == []

    def test_injects_when_jsx_present(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="/static/page.jsx", kind="jsx"))
        inject_vite_dev_assets(collector)
        urls = [a.url for a in collector.assets_in_slot("scripts")]
        preamble_idx = next(i for i, u in enumerate(urls) if u.startswith("data:"))
        vite_idx = next(i for i, u in enumerate(urls) if "@vite/client" in u)
        jsx_idx = next(i for i, u in enumerate(urls) if "page.jsx" in u)
        assert preamble_idx < vite_idx < jsx_idx
        assert "RefreshRuntime" in _decode_preamble_data_url(urls[preamble_idx])

    def test_uses_env_origin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VITE_ORIGIN", "http://example.test:4242")
        collector = StaticCollector()
        collector.add(StaticAsset(url="/static/page.jsx", kind="jsx"))
        inject_vite_dev_assets(collector)
        urls = [a.url for a in collector.assets_in_slot("scripts")]
        assert "http://example.test:4242/@vite/client" in urls
        preamble = next(u for u in urls if u.startswith("data:"))
        decoded = _decode_preamble_data_url(preamble)
        assert 'from "http://example.test:4242/@react-refresh"' in decoded


class TestColumnCardsContext:
    def test_returns_column_cards(self, two_columns: tuple[Column, Column]) -> None:
        col_a, _ = two_columns
        card = Card.objects.create(column=col_a, title="A", position=0)
        mod = _load("column/component.py")
        qs = mod.cards(col_a)
        assert card in qs


class TestCardExcerptComponent:
    def test_delegates_to_model_property(
        self, two_columns: tuple[Column, Column]
    ) -> None:
        col_a, _ = two_columns
        card = Card.objects.create(column=col_a, title="T", body="Hi", position=0)
        mod = _load("card/component.py")
        assert mod.excerpt(card) == "Hi"


class TestCreateCardHandlerRace:
    """Authoritative WIP check inside the handler rejects a racing post."""

    def test_handler_returns_400_when_limit_filled_after_clean(
        self,
        two_columns: tuple[Column, Column],
    ) -> None:
        col_a, _ = two_columns
        col_a.wip_limit = 2
        col_a.save()
        # Form sees one free slot.
        form = CreateCardForm(data={"column_id": str(col_a.pk), "title": "Late"})
        Card.objects.create(column=col_a, title="One", position=0)
        assert form.is_valid()
        # Concurrent post fills the slot before the handler runs.
        Card.objects.create(column=col_a, title="Two", position=1)

        page_module = _load_handler_module()
        response = page_module.create_card(form)
        assert response.status_code == 400


def _load_handler_module():
    spec = importlib.util.spec_from_file_location(
        "_test_board_page",
        Path(__file__).parent.parent
        / "kanban"
        / "boards"
        / "board"
        / "[int:id]"
        / "page.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod
