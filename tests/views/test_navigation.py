import json
import re
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.http import HttpResponseBase
from django.test import RequestFactory

from next.pages import page
from next.pages.layouts import SLOT
from next.pages.loaders import LayoutTemplateLoader
from next.pages.responses import Purpose, purpose_of
from next.pages.signals import page_rendered
from next.partial.headers import VARY_HEADERS
from next.router import (
    CutKind,
    navigation_rendered,
    prefetch_shed,
    router_clear,
    router_cleared,
)
from next.suspense.manager import suspense_manager
from next.suspense.markers import Capacity
from next.suspense.policy import open_streams, streaming
from next.testing import (
    NextClient,
    capture_signals,
    declared_layouts,
    override_next_settings,
)
from tests.support import routed, unified_view


ROOT_LAYOUT = (
    '<!doctype html><html lang="en"><head>{% metadata %}{% collect_styles %}</head>'
    '<body><header>{% zone "flash" %}<p>{{ flash|default:"none" }}</p>{% endzone %}'
    "</header>{% template %}{% collect_scripts %}</body></html>"
)
SECTION_LAYOUT = "<section><h2>Section {{ title }}</h2>{% template %}</section>"
_LAYOUTS = re.compile(r'"\$layouts":(\[.*?\]\])')
_FACTORY = RequestFactory()
_DOWN = "down"


def _page(directory: Path, body: str, source: str = "") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        "from next.pages import context, page\n"
        "@context('title')\n"
        "def title():\n"
        "    return 'same'\n" + source
    )
    (directory / "template.djx").write_text(body)
    return page_file


@pytest.fixture()
def pages(tmp_path: Path) -> dict[str, Path]:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(ROOT_LAYOUT)
    section = root / "sec"
    section.mkdir()
    (section / "layout.djx").write_text(SECTION_LAYOUT)
    built = {
        "a": _page(section / "a", "<h1>A</h1>"),
        "b": _page(section / "b", "<h1>B</h1>"),
        "other": _page(root / "other", "<h1>other</h1>"),
        "slow": _page(section / "slow", "<h1>{{ late }}</h1>", "late = 1\n"),
        "opted": _page(
            section / "opted",
            "<h1>opted</h1>",
            "page.router(prefetch=False, snapshot=False)\n",
        ),
        "hard": _page(
            section / "hard", "<h1>hard</h1>", "page.router(navigate=False)\n"
        ),
    }
    (section / "slow" / "loading.djx").write_text("<p>loading</p>")
    return built


@pytest.fixture()
def router_on() -> Iterator[None]:
    with override_next_settings(ROUTER={"ENABLED": True}):
        yield


@pytest.fixture()
def browser(pages: dict[str, Path], tmp_path: Path) -> Iterator[NextClient]:
    with routed(tmp_path / "pages", ROUTER={"ENABLED": True}):
        yield NextClient()


def _ids(browser: NextClient, path: str) -> list[str]:
    declared = declared_layouts(browser.get(path).content.decode())
    return [key.split(".")[0] for key in declared.split(",")]


def _get(page_file: Path, path: str = "/", **headers: str) -> HttpResponseBase:
    return unified_view(page, page_file)(_FACTORY.get(path, headers=headers))


def _navigate(
    page_file: Path, path: str = "/sec/b/", layouts: str | None = None, **headers: str
) -> HttpResponseBase:
    sent = {
        "X-Next-Request": "1",
        "X-Next-Navigate": "1",
        "X-Next-Origin": "/sec/a/",
        "Sec-Fetch-Site": "same-origin",
        **headers,
    }
    if layouts is not None:
        sent["X-Next-Layouts"] = layouts
    return _get(page_file, path, **sent)


def _failing(seen: list[int]):
    """Return a step that notes the open streams, then raises."""

    def fail(*args: object, **kwargs: object) -> None:
        seen.append(open_streams.open)
        raise RuntimeError(_DOWN)

    return fail


def _layouts(response: HttpResponseBase) -> list[list[str]]:
    found = _LAYOUTS.search(response.content.decode())
    assert found is not None
    return json.loads(found.group(1))


def _declare(layouts: list[list[str]]) -> str:
    return ",".join(f"{ident}.{digest}" for ident, digest, _seal in layouts)


def _envelope(response: HttpResponseBase) -> dict[str, object]:
    return json.loads(response.content)


def _ops(response: HttpResponseBase) -> list[tuple[str, object]]:
    ops = _envelope(response)["ops"]
    assert isinstance(ops, list)
    return [(op["op"], op.get("target")) for op in ops]


@pytest.mark.usefixtures("router_on")
class TestDocument:
    """A document under the router carries sealed slots and its layout chain."""

    def test_slots_are_sealed_and_listed(self, pages: dict[str, Path]) -> None:
        response = _get(pages["a"], "/sec/a/")
        html = response.content.decode()
        layouts = _layouts(response)
        assert len(layouts) == 2
        root, section = layouts
        assert len(root[1]) == 32
        assert len(section[1]) == 16
        assert root[2] == section[2]
        assert f"<!--{SLOT} {section[0]} {section[2]}--><h1>A</h1>" in html
        assert '"$router":{"scope":"all"' in html

    def test_ids_and_digests_hold_across_renders(self, pages: dict[str, Path]) -> None:
        first = _layouts(_get(pages["a"], "/sec/a/"))
        second = _layouts(_get(pages["a"], "/sec/a/"))
        assert [entry[:2] for entry in first] == [entry[:2] for entry in second]
        assert first[0][2] != second[0][2]

    def test_a_shared_chrome_digests_alike_on_two_pages(
        self, pages: dict[str, Path]
    ) -> None:
        first = _layouts(_get(pages["a"], "/sec/a/"))
        second = _layouts(_get(pages["b"], "/sec/b/"))
        assert [entry[:2] for entry in first] == [entry[:2] for entry in second]

    def test_the_document_names_its_purpose(self, pages: dict[str, Path]) -> None:
        with capture_signals(page_rendered) as recorder:
            _get(pages["a"], "/sec/a/")
            _get(pages["a"], "/sec/a/", **{"Sec-Purpose": "prefetch"})
        purposes = [event.kwargs["purpose"] for event in recorder.events]
        assert purposes == [Purpose.DOCUMENT, Purpose.SPECULATION]

    def test_a_page_out_of_snapshots_says_so(self, pages: dict[str, Path]) -> None:
        assert _get(pages["opted"], "/sec/opted/")["X-Next-Router"] == "no-snapshot"


class TestRouterOff:
    """With the router off nothing of it reaches composition or a document."""

    def test_composition_and_document_carry_no_marker(
        self, pages: dict[str, Path]
    ) -> None:
        composed = LayoutTemplateLoader().compose_body("<h1>A</h1>", pages["a"])
        html = _get(pages["a"], "/sec/a/").content.decode()
        assert "next:slot" not in composed
        assert "<!--nx:" not in html
        assert "$router" not in html
        assert "$layouts" not in html

    def test_the_document_is_the_golden_one(self, pages: dict[str, Path]) -> None:
        html = _get(pages["b"], "/sec/b/").content.decode()
        start = html.index("<body>")
        body = html[start : html.index("<script")]
        assert body == (
            '<body><header><div data-next-zone="flash"><p>none</p></div></header>'
            "<section><h2>Section same</h2><h1>B</h1></section>"
        )

    def test_a_navigation_header_is_answered_as_a_page(
        self, pages: dict[str, Path]
    ) -> None:
        request = _FACTORY.get(
            "/sec/b/", headers={"X-Next-Request": "1", "X-Next-Navigate": "1"}
        )
        response = unified_view(page, pages["b"])(request)
        assert response["Content-Type"].startswith("text/html")
        assert purpose_of(request) is Purpose.DOCUMENT


@pytest.mark.usefixtures("router_on")
class TestCut:
    """A navigation keeps the deepest layout whose id and digest match."""

    def test_the_deepest_shared_layout_is_kept(self, browser: NextClient) -> None:
        root, section = _ids(browser, "/sec/a/")
        navigation = browser.navigate("/sec/b/", origin="/sec/a/")
        assert navigation.kind is CutKind.SLOT
        assert navigation.kept == (root, section)
        assert navigation.diverged is None
        assert navigation.envelope is not None
        assert navigation.envelope.targets()[0] == {"slot": section}
        assert navigation.envelope.zone_targets() == ["flash"]
        assert navigation.envelope.ops[0]["html"] == "<h1>B</h1>"

    def test_a_partial_match_keeps_the_root(self, browser: NextClient) -> None:
        root, _section = _ids(browser, "/sec/a/")
        navigation = browser.navigate("/other/", origin="/sec/a/")
        assert navigation.kind is CutKind.SLOT
        assert navigation.kept == (root,)
        assert navigation.envelope is not None
        assert navigation.envelope.targets()[0] == {"slot": root}

    def test_a_differing_root_body_morphs_the_body(
        self, pages: dict[str, Path]
    ) -> None:
        root, section = _layouts(_get(pages["a"], "/sec/a/"))
        declared = f"{root[0]}.{root[1][:16]}{'0' * 16}"
        response = _navigate(pages["b"], layouts=declared)
        assert _ops(response)[0] == ("morph", {"body": True})
        assert len(_envelope(response)["layouts"]) == 2
        assert section[0] in json.dumps(_envelope(response)["layouts"])

    def test_a_differing_head_loads_hard(self, pages: dict[str, Path]) -> None:
        root, _section = _layouts(_get(pages["a"], "/sec/a/"))
        response = _navigate(pages["b"], layouts=f"{root[0]}.{'0' * 32}")
        assert _ops(response) == [("visit", None)]
        assert _envelope(response)["root"] == root[0]

    @pytest.mark.parametrize(
        "layouts",
        [
            pytest.param("garbage", id="garbage"),
            pytest.param(",".join([f"{'a' * 16}.{'b' * 16}"] * 40), id="oversize"),
            pytest.param("x" * 2000, id="over-one-kilobyte"),
        ],
    )
    def test_an_unreadable_declaration_morphs_the_body(
        self, pages: dict[str, Path], layouts: str
    ) -> None:
        assert _ops(_navigate(pages["b"], layouts=layouts))[0] == (
            "morph",
            {"body": True},
        )

    def test_a_forged_id_loads_hard(self, pages: dict[str, Path]) -> None:
        response = _navigate(pages["b"], layouts=f"{'1' * 16}.{'2' * 16}")
        assert _ops(response) == [("visit", None)]

    def test_a_wrong_digest_never_keeps_the_layout(
        self, pages: dict[str, Path]
    ) -> None:
        root, section = _layouts(_get(pages["a"], "/sec/a/"))
        declared = f"{root[0]}.{root[1]},{section[0]}.{'f' * 16}"
        assert _ops(_navigate(pages["b"], layouts=declared))[0] == (
            "morph",
            {"slot": root[0]},
        )

    def test_a_look_alike_comment_in_content_moves_nothing(
        self, pages: dict[str, Path], tmp_path: Path
    ) -> None:
        root, section = _layouts(_get(pages["a"], "/sec/a/"))
        forged = f"<!--{SLOT} {section[0]} deadbeef--><h1>forged</h1>"
        tricked = _page(tmp_path / "pages" / "sec" / "forged", forged)
        response = _navigate(tricked, "/sec/forged/", _declare([root, section]))
        envelope = _envelope(response)
        assert envelope["ops"][0]["target"] == {"slot": section[0]}
        assert envelope["ops"][0]["html"] == forged


@pytest.mark.usefixtures("router_on")
class TestEnvelope:
    """The navigation envelope carries every field the client applies."""

    def test_the_fields(self, pages: dict[str, Path]) -> None:
        declared = _declare(_layouts(_get(pages["a"], "/sec/a/")))
        response = _navigate(pages["b"], layouts=declared)
        envelope = _envelope(response)
        ops = {op["op"]: op for op in envelope["ops"]}
        assert ops["url"] == {"op": "url", "action": "push", "href": "/sec/b/"}
        assert set(ops["meta"]) == {"op", "title", "description", "canonical", "robots"}
        assert ops["context"]["replace"] is True
        assert [entry[2] for entry in envelope["layouts"]] == [None, None]
        assert envelope["root"] == declared.split(".")[0]
        assert {"nonce", "scripts", "consent", "shared"} <= set(envelope)
        assert envelope["shared"] is False
        assert response["Cache-Control"] == "private, no-store"
        varied = {name.strip() for name in response["Vary"].split(",")}
        assert set(VARY_HEADERS) <= varied

    def test_the_same_url_replaces_the_entry(self, browser: NextClient) -> None:
        navigation = browser.navigate("/sec/b/", origin="/sec/b/")
        assert navigation.envelope is not None
        url = next(op for op in navigation.envelope.ops if op["op"] == "url")
        assert url["action"] == "replace"

    def test_the_navigation_is_announced(self, pages: dict[str, Path]) -> None:
        with capture_signals(navigation_rendered, page_rendered) as recorder:
            _navigate(pages["b"], layouts="garbage")
        kinds = {event.signal: event.kwargs for event in recorder.events}
        assert kinds[navigation_rendered]["cut"] == "body"
        assert kinds[navigation_rendered]["streamed"] is False
        assert kinds[page_rendered]["purpose"] is Purpose.NAVIGATION


@pytest.mark.usefixtures("router_on")
class TestGate:
    """What a navigation is answered before any render."""

    def test_another_site_is_refused(self, pages: dict[str, Path]) -> None:
        response = _navigate(pages["b"], **{"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403
        assert response["Cache-Control"] == "private, no-store"

    def test_a_stale_version_conflicts(self, pages: dict[str, Path]) -> None:
        assert _navigate(pages["b"], **{"X-Next-Version": "stale"}).status_code == 409

    def test_a_page_refusing_prefetch(self, pages: dict[str, Path]) -> None:
        response = _navigate(pages["opted"], "/sec/opted/", **{"X-Next-Prefetch": "1"})
        assert response.status_code == 204

    def test_a_page_out_of_navigations_loads_hard(self, pages: dict[str, Path]) -> None:
        assert _ops(_navigate(pages["hard"], "/sec/hard/")) == [("visit", None)]

    def test_a_lagging_pool_sheds_a_prefetch(self, pages: dict[str, Path]) -> None:
        lag = Capacity(4, 4, 9, 2.2)
        with (
            mock.patch.object(suspense_manager, "capacity", return_value=lag),
            capture_signals(prefetch_shed) as recorder,
        ):
            response = _navigate(pages["b"], **{"X-Next-Prefetch": "1"})
        assert response.status_code == 429
        assert response["Retry-After"] == "3"
        (event,) = recorder.events
        assert event.kwargs["waited_ms"] == pytest.approx(2200)

    def test_a_shed_needs_no_listener(self, pages: dict[str, Path]) -> None:
        lag = Capacity(4, 4, 9, 0.5)
        with mock.patch.object(suspense_manager, "capacity", return_value=lag):
            response = _navigate(pages["b"], **{"X-Next-Prefetch": "1"})
        assert response["Retry-After"] == "1"

    def test_a_quiet_pool_renders_the_prefetch(self, pages: dict[str, Path]) -> None:
        quiet = Capacity(4, 0, 0, 0.0)
        with mock.patch.object(suspense_manager, "capacity", return_value=quiet):
            response = _navigate(pages["b"], layouts="", **{"X-Next-Prefetch": "1"})
        assert response.status_code == 200

    def test_a_visit_drops_the_stream(self, pages: dict[str, Path]) -> None:
        with streaming():
            response = _navigate(
                pages["slow"], "/sec/slow/", "", Accept="text/event-stream"
            )
        assert _ops(response) == [("visit", None)]


@pytest.mark.usefixtures("router_on")
class TestDeferred:
    """A prefetch answers the shell, a navigation streams its envelope first."""

    def test_a_prefetch_answers_the_loading_content(
        self, pages: dict[str, Path]
    ) -> None:
        declared = _declare(_layouts(_get(pages["a"], "/sec/a/")))
        with capture_signals(page_rendered) as recorder:
            response = _navigate(
                pages["slow"], "/sec/slow/", declared, **{"X-Next-Prefetch": "1"}
            )
        assert response.status_code == 200
        assert "<p>loading</p>" in response.content.decode()
        assert "late" not in _envelope(response)["ops"][0]["html"]
        assert "complete" not in _envelope(response)
        (event,) = recorder.events
        assert event.kwargs["purpose"] is Purpose.PREFETCH

    def test_a_prefetch_that_skipped_no_job_is_complete(
        self, pages: dict[str, Path]
    ) -> None:
        declared = _declare(_layouts(_get(pages["a"], "/sec/a/")))
        prefetched = _navigate(
            pages["b"], "/sec/b/", declared, **{"X-Next-Prefetch": "1"}
        )
        navigated = _navigate(pages["b"], "/sec/b/", declared)
        assert _envelope(prefetched)["complete"] is True
        assert _ops(prefetched) == _ops(navigated)
        assert "complete" not in _envelope(navigated)
        assert prefetched["Cache-Control"] == "private, no-store"

    def test_a_navigation_streams_after_its_envelope(
        self, pages: dict[str, Path]
    ) -> None:
        declared = _declare(_layouts(_get(pages["a"], "/sec/a/")))
        with streaming():
            response = _navigate(
                pages["slow"], "/sec/slow/", declared, Accept="text/event-stream"
            )
            body = b"".join(response.streaming_content).decode()
        assert response["Content-Type"].startswith("text/event-stream")
        hello, first, *rest = body.split("\n\n")
        assert hello.startswith("event: hello")
        assert first.startswith("event: patch")
        assert '"layouts"' in first
        assert "<p>loading</p>" in first
        assert any("event: end" in frame for frame in rest)

    def test_a_navigation_stream_failing_after_its_render_frees_its_slot(
        self, pages: dict[str, Path]
    ) -> None:
        declared = _declare(_layouts(_get(pages["a"], "/sec/a/")))
        before = open_streams.open
        seen: list[int] = []
        with (
            streaming(),
            mock.patch("next.views.pages.announce", side_effect=_failing(seen)),
            pytest.raises(RuntimeError, match=_DOWN) as raised,
        ):
            _navigate(pages["slow"], "/sec/slow/", declared, Accept="text/event-stream")
        assert raised.tb is not None
        assert seen == [before + 1]
        assert open_streams.open == before


@pytest.mark.usefixtures("router_on")
class TestRedirects:
    """A redirect a navigation meets goes to the client's own fetch."""

    def test_a_render_redirect_passes_through(self, tmp_path: Path) -> None:
        directory = tmp_path / "moved"
        directory.mkdir()
        page_file = directory / "page.py"
        page_file.write_text(
            "from django.shortcuts import redirect\n"
            "def render():\n"
            "    return redirect('/elsewhere/')\n"
        )
        response = _navigate(page_file, "/moved/")
        assert response.status_code == 302
        assert response["Location"] == "/elsewhere/"
        assert response["Cache-Control"] == "private, no-store"
        varied = {name.strip() for name in response["Vary"].split(",")}
        assert set(VARY_HEADERS) <= varied

    @pytest.mark.parametrize(
        ("call", "router"),
        [
            pytest.param("router_clear(request, 'logout')", "clear", id="clear"),
            pytest.param("rotate_token(request)", None, id="csrf"),
        ],
    )
    def test_a_redirect_the_client_must_see_rides_a_visit(
        self, tmp_path: Path, call: str, router: str | None
    ) -> None:
        directory = tmp_path / "out"
        directory.mkdir()
        page_file = directory / "page.py"
        page_file.write_text(
            "from django.http import HttpRequest\n"
            "from django.middleware.csrf import rotate_token\n"
            "from django.shortcuts import redirect\n"
            "from next.router import router_clear\n"
            "def render(request: HttpRequest):\n"
            f"    {call}\n"
            "    return redirect('/elsewhere/')\n"
        )
        response = _navigate(page_file, "/out/")
        assert response.status_code == 200
        assert _envelope(response)["ops"] == [{"op": "visit", "href": "/elsewhere/"}]
        assert response.get("X-Next-Router") == router

    def test_another_render_response_loads_hard(self, tmp_path: Path) -> None:
        directory = tmp_path / "raw"
        directory.mkdir()
        page_file = directory / "page.py"
        page_file.write_text(
            "from django.http import HttpResponse\n"
            "def render():\n"
            "    return HttpResponse('raw')\n"
        )
        assert _ops(_navigate(page_file, "/raw/")) == [("visit", None)]

    def test_a_refusal_after_a_logout_rides_a_visit(self, tmp_path: Path) -> None:
        directory = tmp_path / "gone"
        directory.mkdir()
        page_file = directory / "page.py"
        page_file.write_text(
            "from next.pages import page\n"
            "page.login_required()\n"
            "template = '<p>secret</p>'\n"
        )
        request = _FACTORY.get(
            "/gone/",
            headers={
                "X-Next-Request": "1",
                "X-Next-Navigate": "1",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        router_clear(request, "logout")
        response = unified_view(page, page_file)(request)
        (visit,) = _envelope(response)["ops"]
        assert visit["op"] == "visit"
        assert visit["href"].startswith("/accounts/login/?next=")
        assert response["X-Next-Router"] == "clear"

    def test_a_refusal_passes_through(self, tmp_path: Path) -> None:
        directory = tmp_path / "private"
        directory.mkdir()
        page_file = directory / "page.py"
        page_file.write_text(
            "from next.pages import page\n"
            "page.login_required()\n"
            "template = '<p>secret</p>'\n"
        )
        response = _navigate(page_file, "/private/")
        assert response.status_code == 302
        assert response["Cache-Control"] == "private, no-store"


@pytest.mark.usefixtures("router_on")
class TestClear:
    """Login, logout and `router_clear` send `X-Next-Router: clear`."""

    def test_login_and_logout(self, pages: dict[str, Path]) -> None:
        user = mock.Mock(last_login=None)
        with capture_signals(router_cleared) as recorder:
            request = _FACTORY.get("/sec/a/")
            user_logged_in.send(sender=type(user), request=request, user=user)
            login = unified_view(page, pages["a"])(request)
            request = _FACTORY.get("/sec/a/")
            user_logged_out.send(sender=type(user), request=request, user=user)
            logout = unified_view(page, pages["a"])(request)
        assert login["X-Next-Router"] == "clear"
        assert logout["X-Next-Router"] == "clear"
        assert [event.kwargs["reason"] for event in recorder.events] == [
            "login",
            "logout",
        ]

    def test_a_plain_document_sends_no_directive(self, pages: dict[str, Path]) -> None:
        assert not _get(pages["a"], "/sec/a/").has_header("X-Next-Router")
