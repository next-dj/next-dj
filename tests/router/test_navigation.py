import json
import logging
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

import pytest
from django.conf import settings
from django.contrib.auth.signals import user_logged_in
from django.contrib.sessions.backends.signed_cookies import SessionStore as SignedStore
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.response import HttpResponseBase
from django.middleware.csrf import get_token, rotate_token
from django.test import override_settings

from next.caches import ROUTES
from next.forms.dispatch import FormActionDispatch
from next.intercept import Intercept, intercepts
from next.marks import fragment_marks
from next.pages import page
from next.pages.layouts import SLOT, enclosed
from next.pages.loaders import layout_ids
from next.partial.patches import Envelope
from next.requests import Purpose, next_of
from next.router import Cut, CutKind, Divergence, router_clear, router_cleared
from next.router.declarations import RouterOptions
from next.router.markers import ClientLayouts, LayoutKey, ScannedLayout, ScannedPage
from next.router.navigation import (
    CLEAR,
    DECLARATION_BYTES,
    NO_SNAPSHOT,
    Routing,
    action_marking,
    announce,
    cleared,
    debug_cut,
    describe,
    directives,
    hostlike,
    intercepted,
    layouts_payload,
    marked_links,
    navigation_envelope,
    origin_vary,
    page_marking,
    parse_declaration,
    passed,
    read_shell,
    reporting,
    routing,
    stale,
    stamp,
    visit_envelope,
    zone_marking,
)
from next.router.render import router_render
from next.static.runtime import html_finish
from next.testing import capture_signals, override_next_settings
from tests.support import unified_view
from tests.support.navigation import NAVIGATION, declared_by, get_page, navigate, ops


RESPONSE_ID = "5ea1ed00"
IDENT = "0123456789abcdef"
INNER = "fedcba9876543210"
NAV = '<nav><a href="/docs/">Docs</a><a href="/photo/1/">Photo</a></nav>'
DOCS_MARKED = (
    '<nav><a href="/docs/" aria-current="page" data-next-current="page">Docs</a>'
    '<a href="/photo/1/">Photo</a></nav>'
)
PHOTO_MARKED = (
    '<nav><a href="/docs/">Docs</a>'
    '<a href="/photo/1/" aria-current="page" data-next-current="page">Photo</a></nav>'
)
LINKS_OFF = {"ENABLED": False, "ACTIVE_LINKS": {"ENABLED": False}}
UNPARSABLE = "http://[::1"


class TestParseDeclaration:
    """The declaration header is parsed strictly, anything else counts as none."""

    def test_entries_outer_first(self) -> None:
        raw = f"{IDENT}.{'a' * 32}, {INNER}.{'b' * 16}"
        assert parse_declaration(raw, 32) == ClientLayouts(
            (LayoutKey(IDENT, "a" * 32), LayoutKey(INNER, "b" * 16))
        )

    def test_an_empty_value_declares_no_layout(self) -> None:
        assert parse_declaration(" ", 32) == ClientLayouts(())

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param(None, id="absent"),
            pytest.param("x" * (DECLARATION_BYTES + 1), id="over-the-byte-cap"),
            pytest.param(f"{IDENT}.{'a' * 16},{INNER}.{'b' * 16}", id="over-max"),
            pytest.param(f"{IDENT.upper()}.{'a' * 16}", id="upper-case"),
            pytest.param(f"{IDENT}.{'a' * 20}", id="odd-digest"),
            pytest.param(f"{IDENT}-{'a' * 16}", id="no-dot"),
        ],
    )
    def test_unusable_values(self, raw: str | None) -> None:
        assert parse_declaration(raw, 1) is None


class TestReadShell:
    """The scan finds nested slots by id and response id and stops at the first gap."""

    def _html(self) -> str:
        inner = enclosed(SLOT, RESPONSE_ID, "<p>body</p>", INNER)
        outer = enclosed(SLOT, RESPONSE_ID, f"<section>{inner}</section>", IDENT)
        return f"<html><body><nav>x</nav>{outer}</body></html>"

    def test_nested_slots_without_digests(self) -> None:
        html = self._html()
        shell = read_shell(html, RESPONSE_ID, [IDENT, INNER])
        assert [layout.ident for layout in shell.layouts] == [IDENT, INNER]
        start, end = shell.layouts[1].slot
        assert html[start:end] == "<p>body</p>"
        assert shell.root == IDENT
        assert shell.body is not None

    def test_a_missing_slot_ends_the_chain(self) -> None:
        shell = read_shell(self._html(), RESPONSE_ID, [IDENT, "9" * 16, INNER])
        assert [layout.ident for layout in shell.layouts] == [IDENT]

    def test_another_seal_finds_nothing(self) -> None:
        assert read_shell(self._html(), "0badc0de", [IDENT]).layouts == ()
        assert ScannedPage("", RESPONSE_ID, ()).root is None

    def test_payload_seals_only_the_layouts_below_the_cut(self) -> None:
        shell = ScannedPage(
            "",
            RESPONSE_ID,
            (
                ScannedLayout(IDENT, "d1", (0, 0), (0, 0)),
                ScannedLayout(INNER, "d2", (0, 0), (0, 0)),
            ),
        )
        assert layouts_payload(shell, 1) == [
            [IDENT, "d1", None],
            [INNER, "d2", RESPONSE_ID],
        ]


class TestDescribe:
    """The DEBUG header names the cut."""

    @pytest.mark.parametrize(
        ("cut", "text"),
        [
            pytest.param(
                Cut(CutKind.SLOT, (IDENT, INNER)),
                f"kept {IDENT},{INNER} slot {INNER}",
                id="slot",
            ),
            pytest.param(
                Cut(CutKind.SLOT, (IDENT,), INNER, Divergence.DIGEST),
                f"kept {IDENT} slot {IDENT}, diverged {INNER} digest",
                id="slot-diverged",
            ),
            pytest.param(Cut(CutKind.BODY), "body_morph", id="body"),
            pytest.param(
                Cut(CutKind.VISIT, (), IDENT, Divergence.HEAD), "visit head", id="head"
            ),
            pytest.param(Cut(CutKind.VISIT), "visit id", id="visit"),
            pytest.param(
                Cut(CutKind.BODY, (), IDENT, Divergence.CLEAR),
                f"body_morph, diverged {IDENT} clear",
                id="cleared",
            ),
        ],
    )
    def test_the_text(self, cut: Cut, text: str) -> None:
        assert describe(cut) == text

    def test_only_debug_names_it(self) -> None:
        response = HttpResponse()
        debug_cut(response, Cut(CutKind.BODY))
        assert not response.has_header("X-Next-Navigate")
        with override_settings(DEBUG=True):
            debug_cut(response, Cut(CutKind.BODY))
        assert response["X-Next-Navigate"] == "body_morph"

    def test_no_listener_no_signal(self) -> None:
        announce(HttpRequest(), Path("page.py"), Cut(CutKind.BODY), streamed=False)


class TestDivergenceLog:
    """Under DEBUG the first digest divergence of each layout is logged at INFO."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> Iterator[None]:
        reporting._noted.clear()
        yield
        reporting._noted.clear()

    @pytest.fixture()
    def page_file(self, tmp_path: Path) -> Path:
        (tmp_path / "layout.djx").write_text("<html><body>{% template %}</body></html>")
        section = tmp_path / "sec"
        (section / "sub").mkdir(parents=True)
        (section / "layout.djx").write_text(
            "<section>{{ tab }}{% template %}</section>"
        )
        (section / "sub" / "layout.djx").write_text("<p>no slot</p>")
        page_file = section / "sub" / "page.py"
        page_file.write_text('template = "ok"\n')
        return page_file

    def _logged(
        self, caplog: pytest.LogCaptureFixture, page_file: Path, *cuts: Cut
    ) -> list[str]:
        with (
            override_settings(DEBUG=True),
            caplog.at_level(logging.INFO, "next.router"),
        ):
            for cut in cuts:
                announce(HttpRequest(), page_file, cut, streamed=False)
        return [record.getMessage() for record in caplog.records]

    def test_a_digest_divergence_names_the_layout_once(
        self, page_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        outer, inner = layout_ids(page_file)
        cut = Cut(CutKind.SLOT, (outer,), inner, Divergence.DIGEST)

        (message,) = self._logged(caplog, page_file, cut, cut)

        assert f"layout {page_file.parents[1] / 'layout.djx'} differs" in message
        assert f"({describe(cut)})" in message
        assert "{% zone %}" in message

    def test_a_route_reload_logs_it_again(
        self, page_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cut = Cut(CutKind.BODY, (), IDENT, Divergence.DIGEST)
        self._logged(caplog, page_file, cut)
        ROUTES.move()

        assert len(self._logged(caplog, page_file, cut)) == 2

    def test_an_id_outside_the_chain_is_named_as_it_is(
        self, page_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cut = Cut(CutKind.BODY, (), IDENT, Divergence.DIGEST)

        (message,) = self._logged(caplog, page_file, cut)

        assert f"layout {IDENT} differs" in message

    def test_another_reason_logs_nothing(
        self, page_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cuts = (
            Cut(CutKind.VISIT, (), IDENT, Divergence.HEAD),
            Cut(CutKind.SLOT, (IDENT,)),
        )

        assert self._logged(caplog, page_file, *cuts) == []

    def test_without_debug_nothing_is_logged(
        self, page_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, "next.router"):
            announce(
                HttpRequest(),
                page_file,
                Cut(CutKind.BODY, (), IDENT, Divergence.DIGEST),
                streamed=False,
            )

        assert caplog.records == []


class TestRouting:
    """The routed render state, and the active-link pass while the router is off."""

    def test_off_yields_nothing(self) -> None:
        with (
            override_next_settings(ROUTER={"ENABLED": False}),
            routing(HttpRequest(), Path("page.py"), Purpose.DOCUMENT) as held,
        ):
            assert held is None
            assert router_render.get() is None

    def test_off_still_marks_the_active_links(self, rf) -> None:
        with (
            override_next_settings(ROUTER={"ENABLED": False}),
            routing(rf.get("/docs/"), Path("page.py"), Purpose.DOCUMENT),
        ):
            finish = html_finish.get()
            assert finish is not None
            assert finish(NAV) == DOCS_MARKED
        assert html_finish.get() is None

    def test_the_page_block_binds_the_pass_and_unbinds_it(self, rf) -> None:
        with page_marking(rf.get("/docs/")) as held:
            finish = html_finish.get()
            assert held is None
            assert finish is not None
            assert finish(NAV) == DOCS_MARKED
        assert html_finish.get() is None

    def test_off_with_the_pass_off_keeps_the_html(self, rf) -> None:
        with (
            override_next_settings(ROUTER=LINKS_OFF),
            routing(rf.get("/docs/"), Path("page.py"), Purpose.DOCUMENT),
        ):
            finish = html_finish.get()
            assert finish is not None
            assert finish(NAV) is NAV

    def test_a_page_of_compiled_containers_marks_no_document(
        self, tmp_path: Path, rf
    ) -> None:
        with (
            override_next_settings(ROUTER={"ENABLED": True}),
            routing(
                rf.get("/docs/"), tmp_path / "page.py", Purpose.DOCUMENT, fragments=True
            ) as held,
        ):
            assert held is not None
            marks = fragment_marks.get()
            assert marks is not None
            assert marks(NAV) == DOCS_MARKED
            assert held.finish(NAV) is NAV
        assert fragment_marks.get() is None

    def test_on_binds_the_render(self, tmp_path: Path, rf) -> None:
        with (
            override_next_settings(ROUTER={"ENABLED": True}),
            routing(rf.get("/"), tmp_path / "page.py", Purpose.NAVIGATE) as held,
        ):
            assert held is not None
            assert router_render.get() is held.render
            assert html_finish.get() is None
            assert held.purpose is Purpose.NAVIGATE
        assert router_render.get() is None

    def test_a_shell_is_read_when_the_injector_never_finished(
        self, tmp_path: Path, rf
    ) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            held = Routing(rf.get("/"), tmp_path / "page.py", Purpose.NAVIGATE)
            html = f"<p>{held.render.mark}</p>"
            shell = held.settled(html)
            assert shell.layouts == ()
            assert held.settled(held.html or "") is shell
            assert held.html == "<p>[]</p>"

    def test_the_router_entry_opens_no_container(self, tmp_path: Path, rf) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            request = rf.get("/docs/")
            held = Routing(request, tmp_path / "page.py", Purpose.DOCUMENT)
            entry = held.render.encoded()
            script = f'<script>({{"$router":{entry},"$layouts":{held.render.mark}}})'
            html = f'<body><p><a href="/docs/">D</a></p>{script}</script></body>'
            with mock.patch.object(request, "get_full_path") as path:
                done = held.finish(html)
        path.assert_not_called()
        assert done == html.replace(held.render.mark, "[]")
        assert '"[data-next-nav]"' in entry

    @pytest.mark.parametrize("before", [True, False])
    def test_a_container_beside_the_router_entry_is_marked(
        self, tmp_path: Path, rf, *, before: bool
    ) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            held = Routing(rf.get("/docs/"), tmp_path / "page.py", Purpose.DOCUMENT)
            script = (
                f'<script>({{"$router":{held.render.encoded()},'
                f'"$layouts":{held.render.mark}}})</script>'
            )
            body = f"{NAV}{script}" if before else f"{script}{NAV}"
            done = held.finish(f"<html><body>{body}</body></html>")
            shell = held.settled(done)
        assert DOCS_MARKED in done
        start, end = shell.body or (0, 0)
        assert done[start:end] == done[len("<html><body>") : -len("</body></html>")]

    @pytest.mark.parametrize("snapshot", [True, False])
    def test_the_render_carries_the_snapshot_option(
        self, tmp_path: Path, rf, *, snapshot: bool
    ) -> None:
        options = RouterOptions(snapshot=snapshot)
        with (
            override_next_settings(ROUTER={"ENABLED": True}),
            mock.patch(
                "next.router.navigation.state.page_router", return_value=options
            ) as read,
        ):
            held = Routing(rf.get("/"), tmp_path / "page.py", Purpose.DOCUMENT)
        read.assert_called_once_with(tmp_path / "page.py")
        assert held.render.snapshot is snapshot

    @pytest.fixture()
    def body_envelope(self, tmp_path: Path) -> Callable[..., Envelope]:
        """Return a builder of the envelope a body cut of `html` answers `request`."""

        def build(
            request: HttpRequest,
            purpose: Purpose,
            html: str,
            *,
            shared: bool,
            complete: bool,
        ) -> Envelope:
            with override_next_settings(ROUTER={"ENABLED": True}):
                held = Routing(request, tmp_path / "page.py", purpose)
                shell = held.settled(html)
                return navigation_envelope(
                    request,
                    held,
                    shell,
                    Cut(CutKind.BODY),
                    shared=shared,
                    complete=complete,
                ).envelope()

        return build

    def test_an_envelope_without_a_collector(self, rf, body_envelope) -> None:
        body = body_envelope(
            rf.get("/x/"),
            Purpose.NAVIGATE,
            "<p>no body</p>",
            shared=True,
            complete=True,
        )
        assert [patch.op for patch in body.ops] == ["visit"]
        assert "complete" not in body.as_dict()

    def test_a_body_morph_with_the_full_fields(self, rf, body_envelope) -> None:
        request = rf.get("/x/")
        rotate_token(request)
        envelope = body_envelope(
            request,
            Purpose.NAVIGATE,
            "<body><p>x</p></body>",
            shared=True,
            complete=False,
        )
        data = envelope.as_dict()
        assert data["ops"][0] == {
            "op": "morph",
            "target": {"body": True},
            "html": "<p>x</p>",
        }
        assert data["shared"] is True
        assert "complete" not in data
        assert "csrf" in data
        assert json.loads(json.dumps(data))["layouts"] == []

    def test_a_token_read_under_the_same_secret_stays_out(
        self, rf, body_envelope
    ) -> None:
        request = rf.get("/x/")
        request.COOKIES[settings.CSRF_COOKIE_NAME] = request.META["CSRF_COOKIE"] = (
            "s" * 32
        )
        get_token(request)
        envelope = body_envelope(
            request,
            Purpose.NAVIGATE,
            "<body><p>x</p></body>",
            shared=True,
            complete=False,
        )
        assert "csrf" not in envelope.as_dict()

    def test_a_complete_prefetch_says_so(self, rf, body_envelope) -> None:
        envelope = body_envelope(
            rf.get("/x/"),
            Purpose.PREFETCH,
            "<body><p>x</p></body>",
            shared=False,
            complete=True,
        )
        data = envelope.as_dict()
        assert data["complete"] is True
        assert list(data)[-1] == "complete"

    def test_an_unrotated_token_sends_no_csrf(self, rf, body_envelope) -> None:
        envelope = body_envelope(
            rf.get("/x/"),
            Purpose.NAVIGATE,
            "<body></body>",
            shared=False,
            complete=False,
        )
        assert envelope.csrf is None

    @pytest.mark.parametrize("origin", [UNPARSABLE, "https://example.com/x/"])
    def test_an_unusable_origin_pushes_the_entry(
        self, rf, body_envelope, origin: str
    ) -> None:
        request = rf.get(
            "/x/", headers={"X-Next-Request": "1", "X-Next-Origin": origin}
        )
        envelope = body_envelope(
            request, Purpose.NAVIGATE, "<body></body>", shared=False, complete=False
        )
        url = [op.as_dict() for op in envelope.ops if op.op == "url"]
        assert url == [{"op": "url", "action": "push", "href": "/x/"}]

    def test_the_seam_keeps_the_html(self, rf) -> None:
        assert marked_links("<nav></nav>", rf.get("/")) == "<nav></nav>"

    def test_a_page_without_a_container_reads_no_path_and_no_host(self, rf) -> None:
        request = rf.get("/docs/")
        html = '<p><a href="/docs/">Docs</a></p>'
        with (
            mock.patch.object(request, "get_full_path") as path,
            mock.patch.object(request, "get_host") as host,
        ):
            assert marked_links(html, request) is html
        path.assert_not_called()
        host.assert_not_called()

    def test_a_page_with_a_container_is_marked(self, rf) -> None:
        assert marked_links(NAV, rf.get("/docs/")) == DOCS_MARKED

    def test_the_document_is_folded_once(self, rf) -> None:
        with mock.patch("next.router.links.fold", side_effect=str.lower) as folded:
            assert marked_links(NAV, rf.get("/docs/")) == DOCS_MARKED
        assert [call.args for call in folded.call_args_list].count((NAV,)) == 1


class TestZoneMarking:
    """Zone HTML is marked for the page the client shows, beneath any layer."""

    def _marks(self, request: HttpRequest) -> Callable[[str], str] | None:
        with zone_marking(request):
            marks = html_finish.get()
        assert html_finish.get() is None
        return marks

    def test_off_binds_no_pass(self, rf) -> None:
        with override_next_settings(ROUTER=LINKS_OFF):
            assert self._marks(rf.get("/docs/")) is None

    def test_a_zone_refresh_marks_its_own_page(self, rf) -> None:
        marks = self._marks(rf.get("/photo/1/", headers={"X-Next-Request": "1"}))
        assert marks is not None
        assert marks(NAV) == PHOTO_MARKED

    def test_a_layer_marks_the_page_beneath(self, rf) -> None:
        request = rf.get(
            "/photo/1/", headers={"X-Next-Request": "1", "X-Next-Origin": "/docs/"}
        )
        marks = self._marks(request)
        assert marks is not None
        assert marks(NAV) == DOCS_MARKED

    def test_an_unparsable_origin_marks_its_own_page(self, rf) -> None:
        request = rf.get(
            "/photo/1/", headers={"X-Next-Request": "1", "X-Next-Origin": UNPARSABLE}
        )
        marks = self._marks(request)
        assert marks is not None
        assert marks(NAV) == PHOTO_MARKED

    def test_html_without_a_container_is_the_same_object(self, rf) -> None:
        marks = self._marks(rf.get("/docs/"))
        assert marks is not None
        html = '<p><a href="/docs/">Docs</a></p>'
        assert marks(html) is html

    def test_html_without_a_container_reads_no_path_and_no_host(self, rf) -> None:
        request = rf.get("/docs/")
        marks = self._marks(request)
        assert marks is not None
        with (
            mock.patch.object(request, "get_full_path") as path,
            mock.patch.object(request, "get_host") as host,
        ):
            marks('<p><a href="/docs/">Docs</a></p>')
        path.assert_not_called()
        host.assert_not_called()

    def test_the_path_is_read_once_and_rooted_links_never_read_the_host(
        self, rf
    ) -> None:
        request = rf.get("/docs/")
        marks = self._marks(request)
        assert marks is not None
        with (
            mock.patch.object(request, "get_full_path", wraps=request.get_full_path),
            mock.patch.object(request, "get_host", wraps=request.get_host),
        ):
            assert marks(NAV) == DOCS_MARKED
            assert marks(NAV) == DOCS_MARKED
            assert request.get_full_path.call_count == 1
            assert request.get_host.call_count == 0

    def test_absolute_links_read_the_host_once_for_every_zone(self, rf) -> None:
        request = rf.get("/docs/")
        marks = self._marks(request)
        assert marks is not None
        html = '<nav><a href="http://testserver/docs/">Docs</a></nav>'
        with mock.patch.object(request, "get_host", wraps=request.get_host):
            first, second = marks(html), marks(html)
            assert request.get_host.call_count == 1
        assert first == second
        assert 'aria-current="page"' in first

    def test_a_marked_zone_varies_on_the_origin(self) -> None:
        assert origin_vary(HttpResponse())["Vary"] == "X-Next-Origin"
        with override_next_settings(ROUTER=LINKS_OFF):
            assert not origin_vary(HttpResponse()).has_header("Vary")


class TestActionMarking:
    """Zone HTML an action renders is marked for the page its form lives on."""

    def _marks(self, request: HttpRequest) -> Callable[[str], str] | None:
        with action_marking(request):
            marks = html_finish.get()
        assert html_finish.get() is None
        return marks

    def test_the_posted_origin_names_the_page(self, rf) -> None:
        request = rf.post("/_next/form/x/", {"_next_form_origin": "/docs/"})
        marks = self._marks(request)
        assert marks is not None
        assert marks(NAV) == DOCS_MARKED

    def test_a_layer_marks_the_page_beneath(self, rf) -> None:
        request = rf.post(
            "/_next/form/x/",
            {"_next_form_origin": "/docs/"},
            headers={"X-Next-Request": "1", "X-Next-Origin": "/photo/1/"},
        )
        marks = self._marks(request)
        assert marks is not None
        assert marks(NAV) == PHOTO_MARKED

    def test_no_origin_binds_no_pass(self, rf) -> None:
        assert self._marks(rf.post("/_next/form/x/")) is None

    def test_off_binds_no_pass(self, rf) -> None:
        request = rf.post("/_next/form/x/", {"_next_form_origin": "/docs/"})
        with override_next_settings(ROUTER=LINKS_OFF):
            assert self._marks(request) is None

    @pytest.mark.django_db()
    def test_the_dispatch_marks_inside_the_action(self, rf) -> None:
        request = rf.post("/_next/form/x/", {"_next_form_origin": "/docs/"})
        seen: list[str] = []

        def transported(*_args: object) -> HttpResponse:
            marks = html_finish.get()
            assert marks is not None
            seen.append(marks(NAV))
            return HttpResponse()

        with mock.patch.object(
            FormActionDispatch, "_transported", side_effect=transported
        ):
            FormActionDispatch.dispatch(mock.Mock(), request, "x", {})
        assert seen == [DOCS_MARKED]
        assert html_finish.get() is None


@pytest.mark.usefixtures("router_on")
class TestPassed:
    """A response a navigation passes on is private, and a hidden redirect a visit."""

    def test_a_plain_redirect_passes_through(self, rf) -> None:
        response = passed(HttpResponseRedirect("/next/"), rf.get("/x/"))
        assert response.status_code == 302
        assert response["Cache-Control"] == "private, no-store"
        assert not response.has_header("X-Next-Router")

    def test_a_cleared_redirect_rides_a_visit(self, rf) -> None:
        request = rf.get("/x/")
        router_clear(request, "logout")
        redirect = HttpResponseRedirect("next/")
        redirect.set_cookie("kept", "1")
        response = passed(redirect, request)
        assert response.status_code == 200
        assert ops(response) == [{"op": "visit", "href": "/x/next/"}]
        assert response["X-Next-Router"] == CLEAR
        assert response.cookies["kept"].value == "1"

    def test_a_rotated_token_rides_a_visit(self, rf) -> None:
        request = rf.get("/x/")
        rotate_token(request)
        response = passed(HttpResponseRedirect("/next/"), request)
        assert ops(response) == [{"op": "visit", "href": "/next/"}]
        assert not response.has_header("X-Next-Router")

    def test_an_offsite_target_visits_outside(self, rf) -> None:
        request = rf.get("/x/")
        router_clear(request, "login")
        response = passed(HttpResponseRedirect("https://id.example.com/a"), request)
        assert ops(response) == [
            {"op": "visit", "href": "https://id.example.com/a", "external": True}
        ]

    def test_a_cleared_refusal_is_stamped(self, rf) -> None:
        request = rf.get("/x/")
        router_clear(request)
        response = passed(HttpResponse(status=403), request)
        assert response.status_code == 403
        assert response["X-Next-Router"] == CLEAR

    def test_a_redirect_without_a_location_passes_through(self, rf) -> None:
        request = rf.get("/x/")
        router_clear(request)
        response = passed(HttpResponse(status=304), request)
        assert response.status_code == 304


class TestCleared:
    """A response that clears the router keeps no layout of the old session."""

    SHELL = ScannedPage("", RESPONSE_ID, (ScannedLayout(IDENT, "d", (0, 0), (0, 0)),))

    def test_a_clear_gives_up_the_kept_layouts(self) -> None:
        request = HttpRequest()
        router_clear(request, "login")
        cut = cleared(request, Cut(CutKind.SLOT, (IDENT,)), self.SHELL)
        assert cut == Cut(CutKind.BODY, (), IDENT, Divergence.CLEAR)

    def test_without_a_clear_the_slot_cut_holds(self) -> None:
        cut = Cut(CutKind.SLOT, (IDENT,))
        assert cleared(HttpRequest(), cut, self.SHELL) is cut

    def test_a_clear_leaves_a_body_morph_as_it_is(self) -> None:
        request = HttpRequest()
        router_clear(request)
        cut = Cut(CutKind.BODY)
        assert cleared(request, cut, self.SHELL) is cut


class TestHostlike:
    """A path opening with `//` loads hard as its single-slash path."""

    def test_the_visit_runs_the_slashes_into_one(self, rf) -> None:
        request = rf.get("/%2F%2Fevil.com/x")
        assert hostlike(request)
        ops = visit_envelope(request).envelope().as_dict()["ops"]
        assert ops == [{"op": "visit", "href": "/evil.com/x"}]

    def test_a_plain_path_is_no_host(self, rf) -> None:
        assert not hostlike(rf.get("/sec/a/"))


class TestDirectives:
    """`X-Next-Router` names a clear and a page out of snapshots."""

    def test_router_clear_marks_once(self) -> None:
        request = HttpRequest()
        with capture_signals(router_cleared) as recorder:
            router_clear(request)
            router_clear(request, "login")
        assert [event.kwargs["reason"] for event in recorder.events] == ["policy"]
        assert directives(request) == (CLEAR,)
        assert directives(request, snapshot=False) == (CLEAR, NO_SNAPSHOT)

    def test_a_read_makes_no_namespace(self) -> None:
        request = HttpRequest()
        assert directives(request) == ()
        assert "next" not in vars(request)

    def test_the_reason_lives_in_the_namespace(self) -> None:
        request = HttpRequest()
        router_clear(request, "login")
        assert next_of(request).router_clear == "login"
        assert next_of(request).purpose is Purpose.DOCUMENT

    def test_a_cycled_session_clears(self, rf) -> None:
        request = rf.get("/")
        request.COOKIES["sessionid"] = "old"
        request.session = mock.Mock(session_key="new")
        with capture_signals(router_cleared) as recorder:
            assert directives(request) == (CLEAR,)
        assert recorder.events[0].kwargs["reason"] == "session"

    @pytest.mark.parametrize(
        "session",
        [
            pytest.param(None, id="no-session"),
            pytest.param(mock.Mock(session_key="same"), id="same-key"),
            pytest.param(SignedStore(), id="signed-cookies"),
        ],
    )
    def test_an_unchanged_session_does_not(self, session: object, rf) -> None:
        request = rf.get("/")
        request.COOKIES["sessionid"] = "same"
        if session is not None:
            request.session = session
        assert directives(request) == ()

    def test_a_fresh_visitor_does_not(self, rf) -> None:
        request = rf.get("/")
        request.session = mock.Mock(session_key="new")
        assert directives(request) == ()

    def test_stamp_needs_the_router(self) -> None:
        request = HttpRequest()
        router_clear(request)
        response = HttpResponse()
        with override_next_settings(ROUTER={"ENABLED": False}):
            stamp(response, request)
        assert not response.has_header("X-Next-Router")
        with override_next_settings(ROUTER={"ENABLED": True}):
            stamp(response, request)
        assert response["X-Next-Router"] == CLEAR

    def test_no_stale_version_without_one(self, rf) -> None:
        request = rf.get("/", headers={"X-Next-Request": "1"})
        assert stale(request) is None

    @pytest.mark.parametrize(("protocol", "status"), [("1", 409), ("2", None)])
    def test_another_wire_protocol_conflicts(self, rf, protocol, status) -> None:
        headers = {"X-Next-Request": "1", "X-Next-Protocol": protocol}
        answer = stale(rf.get("/", headers=headers))
        assert (answer and answer.status_code) == status

    @pytest.mark.django_db()
    def test_an_action_that_logs_in_clears(self, rf) -> None:
        request = rf.post("/_next/form/x/")
        user = mock.Mock(last_login=None)

        def transported(*_args: object) -> HttpResponse:
            user_logged_in.send(sender=type(user), request=request, user=user)
            return HttpResponse()

        with (
            override_next_settings(ROUTER={"ENABLED": True}),
            mock.patch.object(
                FormActionDispatch, "_transported", side_effect=transported
            ),
        ):
            response = FormActionDispatch.dispatch(mock.Mock(), request, "x", {})
        assert response["X-Next-Router"] == CLEAR


@pytest.mark.usefixtures("router_on")
class TestNavigationCut:
    """The cut a routed page view answers a navigation with."""

    def test_a_zone_around_the_slot_is_kept_as_chrome(
        self, layout_pages: dict[str, Path]
    ) -> None:
        response = navigate(
            layout_pages["b"], "/sec/b/", declared_by(layout_pages["a"])
        )
        targets = [op.get("target") for op in ops(response) if op["op"] == "morph"]
        assert len(targets) == 2
        assert "slot" in targets[0]
        assert targets[1] == {"zone": "flash"}
        assert ops(response)[0]["html"] == "<h1>B</h1>"

    def test_a_differing_body_tag_loads_hard(
        self, layout_pages: dict[str, Path]
    ) -> None:
        response = navigate(
            layout_pages["themed"], "/sec/themed/", declared_by(layout_pages["a"])
        )
        assert [op["op"] for op in ops(response)] == ["visit"]

    def test_a_login_keeps_no_layout(self, layout_pages: dict[str, Path], rf) -> None:
        request = rf.get(
            "/sec/b/",
            headers={**NAVIGATION, "X-Next-Layouts": declared_by(layout_pages["a"])},
        )
        user = mock.Mock(last_login=None)
        user_logged_in.send(sender=type(user), request=request, user=user)
        with override_settings(DEBUG=True):
            response = unified_view(page, layout_pages["b"])(request)
        assert ops(response)[0]["target"] == {"body": True}
        assert response["X-Next-Router"] == CLEAR
        assert response["X-Next-Navigate"].endswith(" clear")

    def test_a_host_like_path_loads_its_single_slash_path(
        self, layout_pages: dict[str, Path]
    ) -> None:
        response = navigate(
            layout_pages["a"], "/%2F%2Fevil.com/x", declared_by(layout_pages["a"])
        )
        assert response.status_code == 200
        assert ops(response) == [{"op": "visit", "href": "/evil.com/x"}]

    @pytest.mark.parametrize("zone", [True, False])
    def test_an_unparsable_origin_is_no_origin(
        self, layout_pages: dict[str, Path], *, zone: bool
    ) -> None:
        extra = {"X-Next-Zone": "flash"} if zone else {}
        headers = {**NAVIGATION, "X-Next-Origin": UNPARSABLE, **extra}
        if zone:
            del headers["X-Next-Navigate"]
        response = get_page(layout_pages["b"], "/sec/b/", **headers)
        assert response.status_code == 200

    def test_a_zone_response_varies_on_the_origin(
        self, layout_pages: dict[str, Path]
    ) -> None:
        response = get_page(
            layout_pages["a"],
            "/sec/a/",
            **{"X-Next-Request": "1", "X-Next-Zone": "flash"},
        )
        varied = {name.strip() for name in response["Vary"].split(",")}
        assert "X-Next-Origin" in varied


MODAL = '{% zone "modal" %}<p>modal</p>{% endzone %}'


class TestIntercepted:
    """Under `INTERCEPT = "links"` a link to an intercepted page opens its layer."""

    def _intercept(
        self, layout_pages: dict[str, Path], template: str
    ) -> Iterator[None]:
        modal = layout_pages["a"].parent / "(..)b"
        modal.mkdir()
        (modal / "page.py").write_text("x = 1\n")
        (modal / "template.djx").write_text(template)
        root = layout_pages["a"].parents[2]
        rule = Intercept(layout_pages["b"], modal / "page.py", "(..)")
        with override_next_settings(ROUTER={"ENABLED": True, "INTERCEPT": "links"}):
            intercepts.publish(root, [rule])
            try:
                yield
            finally:
                intercepts.publish(root, [])

    @pytest.fixture()
    def layered(self, layout_pages: dict[str, Path]) -> Iterator[None]:
        yield from self._intercept(layout_pages, MODAL)

    @pytest.fixture()
    def zoneless(self, layout_pages: dict[str, Path]) -> Iterator[None]:
        yield from self._intercept(layout_pages, "<p>modal</p>")

    def _navigate(
        self, layout_pages: dict[str, Path], headers: dict[str, str]
    ) -> HttpResponseBase:
        return get_page(layout_pages["b"], "/sec/b/", **headers)

    @pytest.mark.usefixtures("layered")
    def test_a_link_opens_the_layer(self, layout_pages: dict[str, Path]) -> None:
        response = self._navigate(layout_pages, NAVIGATION)
        assert ops(response) == [
            {"op": "layer.open", "zone": "modal", "href": "/sec/b/"}
        ]
        assert "layouts" not in json.loads(response.content)
        assert response["Cache-Control"] == "private, no-store"

    @pytest.mark.usefixtures("layered")
    def test_a_prefetch_is_refused(self, layout_pages: dict[str, Path]) -> None:
        response = self._navigate(layout_pages, {**NAVIGATION, "X-Next-Prefetch": "1"})
        assert response.status_code == 204

    @pytest.mark.usefixtures("layered")
    @pytest.mark.parametrize("origin", ["/sec/b/", ""])
    def test_the_page_shown_or_no_origin_renders(
        self, layout_pages: dict[str, Path], origin: str
    ) -> None:
        response = self._navigate(layout_pages, {**NAVIGATION, "X-Next-Origin": origin})
        assert "layer.open" not in [op["op"] for op in ops(response)]

    @pytest.mark.usefixtures("zoneless")
    def test_an_intercept_serving_no_zone_renders(
        self, layout_pages: dict[str, Path]
    ) -> None:
        response = self._navigate(layout_pages, NAVIGATION)
        assert "layer.open" not in [op["op"] for op in ops(response)]

    @pytest.mark.usefixtures("layered")
    def test_an_unparsable_origin_renders(self, layout_pages: dict[str, Path]) -> None:
        response = self._navigate(
            layout_pages, {**NAVIGATION, "X-Next-Origin": UNPARSABLE}
        )
        assert "layer.open" not in [op["op"] for op in ops(response)]

    def test_opt_in_renders(self, layout_pages: dict[str, Path], rf) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            assert (
                intercepted(rf.get("/sec/b/", headers=NAVIGATION), layout_pages["b"])
                is None
            )
