import base64
import copy
import importlib.util
import inspect
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command
from django.http import Http404, HttpRequest
from django.test import override_settings
from kanban.backends import ViteManifestBackend
from kanban.demo import seed_demo
from kanban.forms import CreateCardForm, MoveCardForm
from kanban.models import Board, Card, Column
from kanban.providers import BoardProvider, CardProvider, DBoard, DCard
from kanban.signals import inject_vite_dev_assets

from next.deps import ResolutionContext
from next.deps.cache import DependencyCache
from next.static import StaticAsset, StaticCollector


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
        f"_test_{relative.replace('/', '_').replace('.py', '')}", _PIECES / relative
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _param(annotation: object) -> inspect.Parameter:
    return inspect.Parameter(
        "value", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation
    )


def _context(*, request=None, url_kwargs=None) -> ResolutionContext:
    return ResolutionContext(
        request=request,
        form=None,
        url_kwargs=url_kwargs or {},
        context_data={},
        cache=DependencyCache(),
    )


def _post_request(**post) -> HttpRequest:
    request = HttpRequest()
    request.method = "POST"
    request.POST = post  # type: ignore[assignment]
    return request


@pytest.fixture()
def board() -> Board:
    return Board.objects.create(title="Test", slug="test")


@pytest.fixture()
def two_columns(board: Board) -> tuple[Column, Column]:
    a = Column.objects.create(board=board, title="A", position=0)
    b = Column.objects.create(board=board, title="B", position=1)
    return a, b


@pytest.fixture()
def column(two_columns: tuple[Column, Column]) -> Column:
    return two_columns[0]


@pytest.fixture()
def make_card(column: Column) -> Callable[..., Card]:
    def _make(**fields) -> Card:
        return Card.objects.create(
            **{"column": column, "title": "Card", "body": "", "position": 0, **fields}
        )

    return _make


class TestModelStrings:
    def test_board_str(self, board: Board) -> None:
        assert str(board) == "Test"

    def test_column_str(self, column: Column) -> None:
        assert str(column) == "A"

    def test_card_str(self, make_card: Callable[..., Card]) -> None:
        assert str(make_card(title="Hello")) == "Hello"


class TestCardExcerptProperty:
    @pytest.mark.parametrize(
        ("body", "expected"),
        [("Short", "Short"), ("", "")],
        ids=["short_body", "empty_body"],
    )
    def test_body_under_the_limit_is_returned_verbatim(
        self, make_card: Callable[..., Card], body, expected
    ) -> None:
        assert make_card(body=body).excerpt == expected

    def test_long_body_truncated_with_ellipsis(
        self, make_card: Callable[..., Card]
    ) -> None:
        result = make_card(body="x" * 200).excerpt
        assert result.endswith("…")
        assert len(result) <= 100


class TestBoardProvider:
    @pytest.mark.parametrize(
        ("annotation", "expected"),
        [(DBoard[Board], True), (int, False)],
        ids=["dboard_subscript", "plain_int"],
    )
    def test_can_handle(self, annotation, expected) -> None:
        provider = BoardProvider()
        assert provider.can_handle(_param(annotation), _context()) is expected

    def test_resolve_returns_board(self, board: Board) -> None:
        provider = BoardProvider()
        ctx = _context(url_kwargs={"id": board.pk})
        assert provider.resolve(_param(DBoard[Board]), ctx) == board

    def test_resolve_returns_none_without_id(self) -> None:
        provider = BoardProvider()
        assert provider.resolve(_param(DBoard[Board]), _context()) is None

    def test_resolve_raises_404_for_missing_id(self, board: Board) -> None:
        provider = BoardProvider()
        ctx = _context(url_kwargs={"id": 99999})
        with pytest.raises(Http404):
            provider.resolve(_param(DBoard[Board]), ctx)


class TestCardProvider:
    @pytest.mark.parametrize(
        ("annotation", "http_request", "expected"),
        [
            (DCard[Card], _post_request(card_id="7"), True),
            (DCard[Card], _post_request(), False),
            (DCard[Card], None, False),
            (int, None, False),
        ],
        ids=[
            "post_carries_card_id",
            "post_without_card_id",
            "no_request",
            "non_dcard_annotation",
        ],
    )
    def test_can_handle(self, annotation, http_request, expected) -> None:
        provider = CardProvider()
        ctx = _context(request=http_request)
        assert provider.can_handle(_param(annotation), ctx) is expected

    def test_resolve_returns_card_for_post_id(
        self, make_card: Callable[..., Card]
    ) -> None:
        card = make_card(title="Move me")
        provider = CardProvider()
        ctx = _context(request=_post_request(card_id=str(card.pk)))
        assert provider.can_handle(_param(DCard[Card]), ctx)
        assert provider.resolve(_param(DCard[Card]), ctx) == card

    def test_resolve_raises_404_when_missing(self) -> None:
        provider = CardProvider()
        ctx = _context(request=_post_request(card_id="99999"))
        with pytest.raises(Http404):
            provider.resolve(_param(DCard[Card]), ctx)


class TestMoveCardFormClean:
    def test_cross_board_move_rejected(self, make_card: Callable[..., Card]) -> None:
        other_board = Board.objects.create(title="Other", slug="other")
        other_col = Column.objects.create(board=other_board, title="X", position=0)
        card = make_card(title="Stay")

        form = MoveCardForm(
            data={
                "card_id": str(card.pk),
                "target_column_id": str(other_col.pk),
                "target_position": "0",
            }
        )
        assert not form.is_valid()
        assert "across boards" in str(form.errors)

    def test_unknown_card_rejected(self) -> None:
        form = MoveCardForm(
            data={
                "card_id": "99999",
                "target_column_id": "99999",
                "target_position": "0",
            }
        )
        assert not form.is_valid()
        assert "Unknown card" in str(form.errors)

    def test_missing_fields_skip_database_lookups(self) -> None:
        form = MoveCardForm(data={"card_id": "", "target_column_id": ""})
        assert not form.is_valid()
        assert "card_id" in form.errors


class TestCreateCardFormClean:
    def test_wip_limit_blocks_creation(
        self, column: Column, make_card: Callable[..., Card]
    ) -> None:
        column.wip_limit = 1
        column.save()
        make_card(title="One")

        form = CreateCardForm(data={"column_id": str(column.pk), "title": "Two"})
        assert not form.is_valid()
        assert "WIP limit" in str(form.errors)

    def test_unknown_column_rejected(self) -> None:
        form = CreateCardForm(data={"column_id": "99999", "title": "Lost"})
        assert not form.is_valid()
        assert "Unknown column" in str(form.errors)

    def test_blank_form_short_circuits_clean(self) -> None:
        form = CreateCardForm(data={"column_id": "", "title": ""})
        assert not form.is_valid()
        assert "column_id" in form.errors


class TestViteManifestBackendRegisterFile:
    @pytest.mark.parametrize(
        ("source_path", "logical_name", "kind"),
        [
            (Path("/tmp/style.css"), "kanban/style.css", "css"),
            (Path("/tmp/page.jsx"), "kanban/page.jsx", "jsx"),
        ],
        ids=["non_jsx", "jsx_without_dev_origin_or_manifest"],
    )
    def test_delegates_to_super(self, source_path, logical_name, kind) -> None:
        backend = ViteManifestBackend()
        assert logical_name in backend.register_file(source_path, logical_name, kind)

    @pytest.mark.parametrize(
        ("manifest", "logical_name", "expected"),
        [
            (
                json.dumps({"page.jsx": {"file": "assets/page-abc.js"}}),
                "page.jsx",
                "page-abc.js",
            ),
            ("{}", "unknown.jsx", "unknown.jsx"),
        ],
        ids=["hashed_entry", "missing_entry_falls_back_to_super"],
    )
    def test_jsx_with_manifest(
        self, tmp_path: Path, manifest, logical_name, expected
    ) -> None:
        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(manifest)
        backend = ViteManifestBackend(
            {
                "OPTIONS": {
                    "MANIFEST_PATH": str(manifest_path),
                    "VITE_ROOT": str(tmp_path),
                }
            }
        )
        out = backend.register_file(tmp_path / logical_name, logical_name, "jsx")
        assert expected in out

    def test_jsx_dev_origin_returns_dev_url(self) -> None:
        backend = ViteManifestBackend(
            {"OPTIONS": {"DEV_ORIGIN": "http://localhost:5173"}}
        )
        out = backend.register_file(
            Path("/some/path/page.jsx"), "kanban/page.jsx", "jsx"
        )
        assert out == "http://localhost:5173/page.jsx"

    def test_jsx_with_missing_manifest_file_falls_back(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
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
    @pytest.mark.parametrize(
        ("vite_root", "source_path", "expected"),
        [
            ("/vite", "/vite/kanban/page.jsx", "http://localhost:5173/kanban/page.jsx"),
            (
                "/other",
                "/completely/different/component.jsx",
                "http://localhost:5173/component.jsx",
            ),
            ("", "/any/path/page.jsx", "http://localhost:5173/page.jsx"),
        ],
        ids=["inside_vite_root", "outside_vite_root", "no_vite_root"],
    )
    def test_build_dev_url(self, vite_root, source_path, expected) -> None:
        backend = ViteManifestBackend(
            {"OPTIONS": {"DEV_ORIGIN": "http://localhost:5173", "VITE_ROOT": vite_root}}
        )
        assert backend._build_dev_url(Path(source_path)) == expected


class TestViteManifestBackendManifestKey:
    @pytest.mark.parametrize(
        ("config", "source_path", "expected"),
        [
            (
                {"OPTIONS": {"VITE_ROOT": "/vite"}},
                "/vite/kanban/page.jsx",
                "kanban/page.jsx",
            ),
            (
                {"OPTIONS": {"VITE_ROOT": "/other/root"}},
                "/different/component.jsx",
                "component.jsx",
            ),
            (None, "/any/path/component.jsx", "component.jsx"),
        ],
        ids=["inside_vite_root", "outside_vite_root", "no_config"],
    )
    def test_manifest_key(self, config, source_path, expected) -> None:
        backend = ViteManifestBackend(config)
        assert backend._manifest_key(Path(source_path)) == expected


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


def _static_backend_origin(origin: str):
    config = copy.deepcopy(settings.NEXT_FRAMEWORK)
    for backend in config["STATIC_BACKENDS"]:
        backend.setdefault("OPTIONS", {})["DEV_ORIGIN"] = origin
    return override_settings(NEXT_FRAMEWORK=config)


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

    def test_uses_backend_origin(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="/static/page.jsx", kind="jsx"))
        with _static_backend_origin("http://example.test:4242"):
            inject_vite_dev_assets(collector)
        urls = [a.url for a in collector.assets_in_slot("scripts")]
        assert "http://example.test:4242/@vite/client" in urls
        preamble = next(u for u in urls if u.startswith("data:"))
        decoded = _decode_preamble_data_url(preamble)
        assert 'from "http://example.test:4242/@react-refresh"' in decoded

    def test_skips_when_no_dev_origin_configured(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="/static/page.jsx", kind="jsx"))
        with _static_backend_origin(""):
            inject_vite_dev_assets(collector)
        urls = [a.url for a in collector.assets_in_slot("scripts")]
        assert urls == ["/static/page.jsx"]


class TestColumnCardsContext:
    def test_returns_only_the_column_own_cards(
        self, two_columns: tuple[Column, Column], make_card: Callable[..., Card]
    ) -> None:
        col_a, col_b = two_columns
        mine = make_card(title="A")
        theirs = make_card(column=col_b, title="B")
        mod = _load("column/component.py")
        qs = mod.cards(col_a)
        assert mine in qs
        assert theirs not in qs


class TestCardExcerptComponent:
    def test_delegates_to_model_property(self, make_card: Callable[..., Card]) -> None:
        card = make_card(body="Hi")
        mod = _load("card/component.py")
        assert mod.excerpt(card) == "Hi"


class TestCreateCardHandlerRace:
    """Authoritative WIP check inside the handler rejects a racing post."""

    def test_handler_returns_400_when_limit_filled_after_clean(
        self, column: Column, make_card: Callable[..., Card]
    ) -> None:
        column.wip_limit = 2
        column.save()
        form = CreateCardForm(data={"column_id": str(column.pk), "title": "Late"})
        make_card(title="One")
        assert form.is_valid()
        make_card(title="Two", position=1)

        request = HttpRequest()
        response = form.on_valid(request)
        assert response.status_code == 400


class TestDemoSeed:
    """The demo dataset lives in a seed module rather than a data migration."""

    def test_command_creates_the_demo_boards(self) -> None:
        call_command("seed_demo")
        assert set(Board.objects.values_list("slug", flat=True)) == {
            "engineering-roadmap",
            "marketing-launch",
            "old-experiments",
        }
        cards = Card.objects.filter(column__board__slug="engineering-roadmap")
        assert cards.count() == 4

    def test_seeding_twice_keeps_one_copy(self, demo_data: None) -> None:
        seed_demo()
        assert Board.objects.count() == 3
