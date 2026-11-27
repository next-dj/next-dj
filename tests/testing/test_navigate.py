import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from next.partial.headers import STREAM_ACCEPT
from next.router import CutKind, Divergence
from next.router.markers import MAX_LAYOUTS
from next.testing import NextClient, declared_layouts
from tests.support import routed
from tests.support.streams import streaming


_IDENT = "a" * 16
_DIGEST = "b" * 16


def _document(chain: object) -> str:
    return (
        '<script type="application/json" data-next-layouts>'
        f"{json.dumps(chain)}</script>"
    )


@pytest.fixture()
def pages(layout_pages: dict[str, Path]) -> Path:
    """Return the root of the shared page tree."""
    return layout_pages["other"].parents[1]


@pytest.fixture()
def browser(pages: Path) -> Iterator[NextClient]:
    with routed(pages, ROUTER={"ENABLED": True}):
        yield NextClient()


class TestDeclaredLayouts:
    """`declared_layouts` reads the chain a document shows as the client declares it."""

    def test_a_routed_document_declares_its_chain(self, browser: NextClient) -> None:
        declared = declared_layouts(browser.get("/sec/a/").content.decode())
        keys = declared.split(",")
        assert len(keys) == 2
        assert all(len(key.split(".")[0]) == 16 for key in keys)

    def test_a_document_without_the_router_raises(self, pages: Path) -> None:
        with routed(pages):
            html = NextClient().get("/sec/a/").content.decode()
        with pytest.raises(LookupError, match="no layouts block"):
            declared_layouts(html)

    def test_the_chain_is_trimmed_to_the_byte_bound(self) -> None:
        declared = declared_layouts(_document([[_IDENT, _DIGEST, "s"]] * 40))
        assert len(declared) <= 1024
        assert len(declared.split(",")) == 30

    def test_the_chain_is_trimmed_to_max_layouts(self) -> None:
        declared = declared_layouts(_document([["a", "b", None]] * (MAX_LAYOUTS + 3)))
        assert declared.split(",") == ["a.b"] * MAX_LAYOUTS


class TestNavigate:
    """`navigate` sends what the client router sends and reads the cut it lands on."""

    def test_the_origin_document_declares_the_layouts(
        self, browser: NextClient
    ) -> None:
        shown = declared_layouts(browser.get("/sec/a/").content.decode())
        navigation = browser.navigate("/sec/b/", origin="/sec/a/")
        assert navigation.kind is CutKind.SLOT
        assert navigation.kept == tuple(key.split(".")[0] for key in shown.split(","))
        assert navigation.layouts == shown
        request = navigation.response.wsgi_request
        assert request.headers["X-Next-Origin"] == "/sec/a/"
        assert request.headers["Sec-Fetch-Site"] == "same-origin"
        assert request.headers["X-Next-Layouts"] == shown
        assert "X-Next-Prefetch" not in request.headers

    def test_a_given_declaration_is_sent_as_is(self, browser: NextClient) -> None:
        navigation = browser.navigate("/sec/b/", origin="/sec/a/", layouts="garbage")
        assert navigation.kind is CutKind.BODY
        assert navigation.reason is Divergence.DECLARATION
        assert navigation.response.wsgi_request.headers["X-Next-Layouts"] == "garbage"

    def test_no_origin_declares_nothing(self, browser: NextClient) -> None:
        navigation = browser.navigate("/sec/b/")
        assert navigation.kind is CutKind.VISIT
        assert navigation.kept == ()
        request = navigation.response.wsgi_request
        assert request.headers["X-Next-Layouts"] == ""
        assert "X-Next-Origin" not in request.headers

    def test_a_page_off_the_router_loads_hard(self, browser: NextClient) -> None:
        navigation = browser.navigate("/sec/hard/", origin="/sec/a/")
        assert navigation.kind is CutKind.VISIT
        assert navigation.envelope is not None
        assert navigation.envelope.op_verbs() == ["visit"]

    def test_a_refused_prefetch_cuts_nothing(self, browser: NextClient) -> None:
        navigation = browser.navigate("/sec/opted/", origin="/sec/a/", prefetch=True)
        assert navigation.response.status_code == 204
        assert navigation.response.wsgi_request.headers["X-Next-Prefetch"] == "1"
        assert navigation.kind is None
        assert navigation.envelope is None
        assert navigation.layouts == ""

    def test_a_streamed_navigation_reads_its_opening_envelope(
        self, browser: NextClient
    ) -> None:
        with streaming():
            navigation = browser.navigate(
                "/sec/slow/", origin="/sec/a/", headers={"Accept": STREAM_ACCEPT}
            )
        assert navigation.stream is not None
        assert navigation.stream.ended
        assert navigation.envelope is not None
        assert "<p>loading</p>" in navigation.envelope.ops[0]["html"]
        assert navigation.kind is CutKind.SLOT

    def test_a_streamed_navigation_finds_a_chunk_by_its_name(
        self, browser: NextClient, pages: Path
    ) -> None:
        with streaming():
            navigation = browser.navigate(
                "/sec/slow/", origin="/sec/a/", headers={"Accept": STREAM_ACCEPT}
            )
        stream = navigation.stream
        assert stream is not None
        named = str(pages / "sec" / "slow" / "loading.djx")
        assert stream.boundary(named) is stream.chunks[0]

    def test_a_streamed_origin_document_is_read_whole(
        self, browser: NextClient
    ) -> None:
        with streaming():
            navigation = browser.navigate("/sec/slow/", origin="/sec/slow/")
        assert navigation.kind is CutKind.SLOT
        assert len(navigation.kept) == 2
