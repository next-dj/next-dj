from __future__ import annotations

import json
import re
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING

import pytest
from django.urls import resolve

from next.pages.loaders import layout_ids
from next.pages.manager import page as page_singleton
from next.router import CutKind, Declaration, LayoutKey, VerifiedCut
from next.router.manager import client_router
from next.router.navigation import parse_declaration, read_shell
from next.testing import declared_layouts
from tests.benchmarks.factories import (
    REQUEST_ROUNDS,
    REQUEST_WARMUP_ROUNDS,
    each_round,
    router_get,
)
from tests.support import plain_get, routed, write_page


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from django.http import HttpRequest, HttpResponseBase

    from next.router import ScannedShell


_LINKS = "".join(f'<a href="/docs/topic-{i}/">Topic {i}</a>' for i in range(8))
_ROOT_LAYOUT = (
    '<!DOCTYPE html><html lang="en"><head><title>Docs</title>{% collect_styles %}'
    f"</head><body><nav>{_LINKS}</nav>"
    "{% template %}{% collect_scripts %}</body></html>"
)
_SECTION_LAYOUT = "<section><h2>Section</h2>{% template %}</section>"
_ARTICLE = "<article><h1>{{ request.path }}</h1>" + "<p>row</p>" * 10 + "</article>"
_PUBLIC = "x = 1\n"
_ROUTER_ON = {"ENABLED": True}
_SEAL = re.compile(r"<!--nx:s [0-9a-f]{16} ([0-9a-f]{8})-->")
_MAX_LAYOUTS = 32
_COLD_ROUNDS = 50
_COLD_WARMUP_ROUNDS = 5


@contextmanager
def _site(root: Path) -> Iterator[Callable[[HttpRequest], HttpResponseBase]]:
    """Route two pages of one section under the client router, yielding the target."""
    (root / "layout.djx").write_text(_ROOT_LAYOUT)
    (root / "docs").mkdir()
    (root / "docs" / "layout.djx").write_text(_SECTION_LAYOUT)
    write_page(root, "docs/topic-1", _PUBLIC, body=_ARTICLE)
    write_page(root, "docs/topic-2", _PUBLIC, body=_ARTICLE)
    with routed(root, ROUTER=_ROUTER_ON):
        yield resolve("/docs/topic-2/").func
        page_singleton.clear_template_caches()


def _document(path: str) -> str:
    """Return the document a browser loading `path` holds."""
    return resolve(path).func(plain_get(path)).content.decode()


def _shell(root: Path, html: str) -> ScannedShell:
    """Return the digested shell of a rendered document of the target page."""
    found = _SEAL.search(html)
    assert found is not None
    ids = layout_ids(root / "docs" / "topic-2" / "page.py")
    return read_shell(html, found[1], ids, client_router.digest())


def _declared(shell: ScannedShell, *, stale: bool = False) -> Declaration:
    """Return the declaration of a client showing `shell`, its last digest moved."""
    keys = [LayoutKey(layout.ident, layout.digest) for layout in shell.layouts]
    if stale:
        keys[-1] = LayoutKey(keys[-1].ident, "0" * 16)
    return Declaration(tuple(keys))


class TestBenchNavigation:
    """A link followed by the client router, against the same page as a document."""

    @pytest.mark.benchmark(group="router.navigation")
    def test_full_document(self, tmp_path: Path, benchmark) -> None:
        """The hard load, which digests both layouts and marks the current link."""
        request = plain_get("/docs/topic-2/")
        with _site(tmp_path) as view:
            response = view(request)
            assert b'"$layouts":[[' in response.content
            assert b'aria-current="page"' in response.content
            benchmark.pedantic(
                view,
                setup=each_round(partial(plain_get, "/docs/topic-2/")),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="router.navigation")
    def test_soft_navigation_keeps_the_section(self, tmp_path: Path, benchmark) -> None:
        """Both layouts match the declaration, so the response morphs the inner slot."""
        with _site(tmp_path) as view:
            layouts = declared_layouts(_document("/docs/topic-1/"))
            request = router_get("/docs/topic-2/", "/docs/topic-1/", layouts)
            ops = json.loads(view(request).content)["ops"]
            assert ops[0]["op"] == "morph"
            assert ops[0]["target"] == {"slot": layouts.rsplit(",", 1)[1][:16]}
            assert "<nav>" not in ops[0]["html"]
            benchmark.pedantic(
                view,
                setup=each_round(
                    partial(router_get, "/docs/topic-2/", "/docs/topic-1/", layouts)
                ),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="router.navigation")
    def test_soft_navigation_keeps_the_root_alone(
        self, tmp_path: Path, benchmark
    ) -> None:
        """A stale section digest, so the section is sent again inside the root slot."""
        with _site(tmp_path) as view:
            layouts = declared_layouts(_document("/docs/topic-1/"))
            stale = layouts[:-16] + "0" * 16
            request = router_get("/docs/topic-2/", "/docs/topic-1/", stale)
            ops = json.loads(view(request).content)["ops"]
            assert ops[0]["target"] == {"slot": layouts[:16]}
            assert "<section>" in ops[0]["html"]
            benchmark.pedantic(
                view,
                setup=each_round(
                    partial(router_get, "/docs/topic-2/", "/docs/topic-1/", stale)
                ),
                rounds=REQUEST_ROUNDS,
                warmup_rounds=REQUEST_WARMUP_ROUNDS,
            )

    @pytest.mark.benchmark(group="router.navigation")
    def test_soft_navigation_cold_composition(self, tmp_path: Path, benchmark) -> None:
        """The kept-layout navigation with the composed layers dropped every round."""
        with _site(tmp_path) as view:
            layouts = declared_layouts(_document("/docs/topic-1/"))
            request = router_get("/docs/topic-2/", "/docs/topic-1/", layouts)
            view(request)

            benchmark.pedantic(
                view,
                setup=each_round(
                    partial(router_get, "/docs/topic-2/", "/docs/topic-1/", layouts),
                    before=page_singleton.clear_template_caches,
                ),
                rounds=_COLD_ROUNDS,
                warmup_rounds=_COLD_WARMUP_ROUNDS,
            )


class TestBenchCut:
    """The declaration a navigation sends, read and verified against the page."""

    @pytest.mark.benchmark(group="router.cut")
    def test_parse_declaration(self, tmp_path: Path, benchmark) -> None:
        with _site(tmp_path):
            layouts = declared_layouts(_document("/docs/topic-1/"))
        declared = benchmark(parse_declaration, layouts, _MAX_LAYOUTS)
        assert declared is not None
        assert len(declared.keys) == 2

    @pytest.mark.benchmark(group="router.cut")
    def test_read_shell_digests_the_layouts(self, tmp_path: Path, benchmark) -> None:
        """The sealed slot markers found and the chrome of each layout digested."""
        with _site(tmp_path):
            html = _document("/docs/topic-2/")
            found = _SEAL.search(html)
            assert found is not None
            ids = layout_ids(tmp_path / "docs" / "topic-2" / "page.py")
            backend = client_router.digest()
            shell = benchmark(read_shell, html, found[1], ids, backend)
        assert [len(layout.digest) for layout in shell.layouts] == [32, 16]

    @pytest.mark.benchmark(group="router.cut")
    def test_read_shell_bounds_alone(self, tmp_path: Path, benchmark) -> None:
        """The second read of a response, which finds the markers and digests none."""
        with _site(tmp_path):
            html = _document("/docs/topic-2/")
            found = _SEAL.search(html)
            assert found is not None
            ids = layout_ids(tmp_path / "docs" / "topic-2" / "page.py")
            shell = benchmark(read_shell, html, found[1], ids)
        assert len(shell.layouts) == 2

    @pytest.mark.benchmark(group="router.cut")
    def test_verified_cut_keeps_the_chain(self, tmp_path: Path, benchmark) -> None:
        """Every declared id and digest matches, so the deepest slot is the cut."""
        with _site(tmp_path):
            shell = _shell(tmp_path, _document("/docs/topic-2/"))
        declared = _declared(shell)
        cut = benchmark(VerifiedCut().cut, declared, shell)
        assert cut.kind is CutKind.SLOT
        assert len(cut.kept) == 2

    @pytest.mark.benchmark(group="router.cut")
    def test_verified_cut_on_a_stale_digest(self, tmp_path: Path, benchmark) -> None:
        """The inner layout changed since the client rendered it, so the root stays."""
        with _site(tmp_path):
            shell = _shell(tmp_path, _document("/docs/topic-2/"))
        declared = _declared(shell, stale=True)
        cut = benchmark(VerifiedCut().cut, declared, shell)
        assert cut.kind is CutKind.SLOT
        assert len(cut.kept) == 1
