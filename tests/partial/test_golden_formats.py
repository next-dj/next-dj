import math
import os
from collections.abc import AsyncGenerator, Callable, Generator, Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.test import RequestFactory, override_settings

from next.boundaries.calls import Addressing, BoundarySite
from next.boundaries.failures import DebugBoundaryFailure, ErrorKind
from next.boundaries.options import DeferMode
from next.defer import PartialStream
from next.defer.coordinator import Finish, Totals
from next.defer.ledger import ResponseJobs
from next.defer.markers import Caps, Delta, Job, Piece, Rendered
from next.defer.response import Chunks, Payloads, shell, wrappers
from next.partial.envelope import Envelope, Patch
from next.static.runtime import NextScriptBuilder
from next.testing import NextClient, declared_layouts
from tests.partial.golden_support import GOLDEN_DIR
from tests.support import routed
from tests.support.boundaries import COMPONENT_SITE, make_call, make_job
from tests.support.navigation import write_layout_page


pytestmark = pytest.mark.django_db

_UPDATE = os.environ.get("GOLDEN_UPDATE") == "1"
_RESPONSE_ID = "5ea1c0de"
_PAGE = BoundarySite(Addressing.PAGE, "shop/page.djx", DeferMode.STREAM, "layout.djx")
_BUILDER = NextScriptBuilder("/static/next/next.min.js")


class _Listed:
    """The batches of a stream listed up front, all settled before the end."""

    def __init__(self, *batches: tuple[Piece, ...]) -> None:
        self.batches = batches
        self.failed = False
        self.finish = Finish(aborted=False, cut=False, totals=Totals())

    def pinning_rule(self) -> None:
        return None

    def unsettled(self) -> tuple[Piece, ...]:
        return ()

    def run(self, **_: object) -> Generator[tuple[Piece, ...], None, None]:
        yield from self.batches

    async def arun(self) -> AsyncGenerator[tuple[Piece, ...], None]:
        for batch in self.batches:
            yield batch


def _session() -> ResponseJobs:
    return ResponseJobs(
        Caps.from_settings(), response_id=_RESPONSE_ID, deadline=math.inf
    )


def _job(
    session: ResponseJobs,
    key: str,
    site: BoundarySite = COMPONENT_SITE,
    parent: str = "",
) -> Job:
    return make_job(session, "", key=key, site=site, parent=parent or None)


def _wrapper(job: Job, loading: str = "<p>loading</p>") -> str:
    return shell(job.id, make_call("div", job.key), loading)


def _document(rf: RequestFactory, html: str, *pieces: Piece) -> str:
    """Return a shell and the chunks the page stream writes for `pieces`, then its end."""
    chunks = Chunks(
        rf.get("/"),
        shell=wrappers(html, _RESPONSE_ID),
        collector=None,
        builder=_BUILDER,
        nonce=None,
    )
    return html + "".join(map(chunks.script, pieces)) + chunks.end() + "\n"


def _chunk_ready(rf: RequestFactory) -> str:
    session = _session()
    job = _job(session, "card")
    rendered = Rendered(
        "<article><h2>Ready</h2><p>Привет &amp; 1 &lt; 2</p></article>", Delta()
    )
    return _document(rf, f"<main>{_wrapper(job)}</main>", Piece(job, rendered))


def _chunk_error(rf: RequestFactory) -> str:
    session = _session()
    job = _job(session, "card")
    failure = DebugBoundaryFailure(
        ErrorKind.TIMEOUT, "c0ffee", ErrorKind.TIMEOUT.status, False, "slow", ""
    )
    rendered = Rendered(
        "<p>timed out</p>", Delta(), kind=ErrorKind.TIMEOUT, failure=failure
    )
    with override_settings(DEBUG=True):
        return _document(rf, f"<main>{_wrapper(job)}</main>", Piece(job, rendered))


def _chunk_nested(rf: RequestFactory) -> str:
    session = _session()
    parent = _job(session, "outer")
    child = _job(session, "inner", parent=parent.id)
    row = shell(child.id, make_call(None, child.key), "<tr><td>wait</td></tr>")
    table = f"<section><table><tbody>{row}</tbody></table></section>"
    pieces = (
        Piece(parent, Rendered(table, Delta())),
        Piece(child, Rendered("<tr><td>cell</td></tr>", Delta()), parent=parent.key),
    )
    return _document(rf, f"<main>{_wrapper(parent)}</main>", *pieces)


def _chunk_noindex(rf: RequestFactory) -> str:
    session = _session()
    job = _job(session, "page", site=_PAGE)
    rendered = Rendered("<p>gone</p>", Delta(), kind=ErrorKind.NOT_FOUND)
    html = f"<html><head><title>t</title></head><body>{_wrapper(job)}"
    return _document(rf, html, Piece(job, rendered))


def _stream_boundary(rf: RequestFactory) -> str:
    session = _session()
    job = _job(session, "card")
    wrapper = _wrapper(job)
    envelope = Envelope("9f3c2e1b", (Patch("morph", {"zone": "results"}, wrapper),))
    request = rf.get("/")
    stream = PartialStream(
        request,
        envelope,
        _Listed((Piece(job, Rendered("<p>filled</p>", Delta())),)),
        Payloads(request, shell=wrappers(wrapper, _RESPONSE_ID), collector=None),
        deadline=math.inf,
    )
    try:
        return b"".join(stream).decode()
    finally:
        stream.close()


@pytest.fixture()
def navigated(
    layout_pages: dict[str, Path], tmp_path: Path
) -> Iterator[Callable[[str, str], str]]:
    """Answer navigations from `/sec/a/` with a fixed response id, as JSON text."""
    del layout_pages
    body = '<h1>s</h1><script src="/w.js"></script>'
    write_layout_page(tmp_path / "pages" / "sec" / "scripted", body)

    def answer(path: str, layouts: str) -> str:
        browser = NextClient()
        shown = declared_layouts(browser.get("/sec/a/").content.decode())
        sent = {"kept": shown, "unread": "garbage"}[layouts]
        with override_settings(DEBUG=path == "/sec/scripted/"):
            navigation = browser.navigate(path, origin="/sec/a/", layouts=sent)
        text = navigation.response.content.decode()
        return text.replace(str(tmp_path.resolve()), "") + "\n"

    with (
        routed(tmp_path / "pages", ROUTER={"ENABLED": True}),
        mock.patch("next.pages.layouts.secrets", token_hex=lambda _: _RESPONSE_ID),
    ):
        yield answer


_CHUNKS: dict[str, Callable[[RequestFactory], str]] = {
    "ready": _chunk_ready,
    "error": _chunk_error,
    "nested": _chunk_nested,
    "noindex": _chunk_noindex,
}


def _pinned(path: Path, text: str) -> None:
    """Compare `text` with the committed fixture, or write it under `GOLDEN_UPDATE`."""
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return
    assert path.read_text(encoding="utf-8") == text, (
        f"{path.name} drifted from what the server writes. "
        "Regenerate with GOLDEN_UPDATE=1 and commit the result."
    )


class TestDocumentChunks:
    """A streamed page writes each fixture of `golden/chunk` byte for byte."""

    @pytest.mark.parametrize("name", list(_CHUNKS))
    def test_the_server_writes_the_fixture(self, rf: RequestFactory, name: str) -> None:
        _pinned(GOLDEN_DIR / "chunk" / f"{name}.html", _CHUNKS[name](rf))


class TestPartialStream:
    """A streamed partial response writes the `golden/stream` frames byte for byte."""

    def test_the_server_writes_the_fixture(self, rf: RequestFactory) -> None:
        _pinned(GOLDEN_DIR / "stream" / "boundary.sse", _stream_boundary(rf))


class TestNavigation:
    """A router navigation answers each `golden/navigation` envelope byte for byte."""

    @pytest.mark.parametrize(
        ("name", "path", "sent"),
        [
            ("slot", "/sec/b/", "kept"),
            ("body", "/sec/b/", "unread"),
            ("visit", "/sec/scripted/", "kept"),
        ],
    )
    def test_the_server_writes_the_fixture(
        self, navigated: Callable[[str, str], str], name: str, path: str, sent: str
    ) -> None:
        _pinned(GOLDEN_DIR / "navigation" / f"{name}.json", navigated(path, sent))
