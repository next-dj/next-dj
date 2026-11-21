import gc
import json
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404
from django.test import RequestFactory, override_settings

from next.components.info import ComponentInfo
from next.components.manager import components_manager
from next.components.trip import TripInput
from next.defer import BoundaryError, DeferMode, ErrorKind
from next.permissions.markers import OPEN, AccessRule, Requirement
from next.static.collector import StaticCollector
from next.suspense import BoundaryTokenError, TokenRejection, reload
from next.suspense.tokens import BoundaryClaims
from next.suspense.tokens.props import DEFAULT_LIMIT
from next.testing import override_next_settings


_PATH = Path("/pages/home/page.py")


def _info(name: str, module: Path | None = None) -> ComponentInfo:
    return ComponentInfo(
        name=name,
        scope_root=Path("/nowhere"),
        scope_relative="",
        template_path=None,
        module_path=module,
        is_simple=module is None,
    )


class _Backend:
    """A two-phase backend scripted per override set, recording what it announces."""

    def __init__(self, answers: dict[tuple, object]) -> None:
        self.answers = answers
        self.opened: list[tuple] = []
        self.announced: list[tuple[TokenRejection, str]] = []

    def open(self, request, token, *, tokens=()):
        self.opened.append(tokens)
        answer = self.answers[tokens]
        if isinstance(answer, BoundaryTokenError):
            raise answer
        return answer

    def settle(self, request, claims, target):
        return claims

    def announce(self, request, error, component=""):
        self.announced.append((error.reason, component))

    def prop_limit(self, tokens=()) -> int:
        return DEFAULT_LIMIT


@pytest.fixture()
def scripted(monkeypatch: pytest.MonkeyPatch):
    def install(answers, candidates, overrides=None):
        backend = _Backend(answers)
        monkeypatch.setattr(reload.suspense_manager, "tokens", lambda: backend)
        monkeypatch.setattr(reload, "page_components", lambda _path: candidates)
        held = overrides or {}
        monkeypatch.setattr(
            reload, "token_overrides", lambda info: held.get(info.name, ())
        )
        return backend

    return install


def _redeem():
    return reload.redeem(RequestFactory().get("/home/"), "t", _PATH, {})


def _refusal(reason: TokenRejection) -> BoundaryTokenError:
    return BoundaryTokenError(reason)


class TestResponses:
    def test_a_refusal_names_nothing(self) -> None:
        response = reload.refused()
        assert response.status_code == 403
        assert response.content == b""
        assert response["Cache-Control"] == "private, no-store"
        assert response["Referrer-Policy"] == "same-origin"
        assert response[reload.RELOAD_HEADER] == reload.REFUSED_SIGNAL

    def test_an_expired_token_asks_for_a_page_reload(self) -> None:
        response = reload.expired()
        assert response.status_code == 409
        assert response[reload.RELOAD_HEADER] == reload.EXPIRED_SIGNAL

    def test_a_reload_answers_reads_and_a_body_transport_post(self) -> None:
        assert reload.methods() == ("GET", "HEAD")
        body = {"TOKENS": {"OPTIONS": {"TRANSPORT": "next.suspense.BodyTransport"}}}
        with override_next_settings(DEFER=body):
            assert reload.methods() == ("GET", "HEAD", "POST")

    def test_another_method_is_a_private_405(self) -> None:
        response = reload.not_allowed(("GET", "HEAD"))
        assert response.status_code == 405
        assert response["Allow"] == "GET, HEAD"
        assert response["Cache-Control"] == "private, no-store"
        assert reload.RELOAD_HEADER not in response

    @pytest.mark.parametrize(
        ("exc", "status"),
        [(Http404(), 404), (PermissionDenied(), 403), (BadRequest(), 400)],
    )
    def test_a_guard_failure_answers_as_a_page_would(
        self, exc: Exception, status: int
    ) -> None:
        response = reload.failed(RequestFactory().get("/"), exc)
        assert response.status_code == status
        assert response["Cache-Control"] == "private, no-store"
        assert response["Referrer-Policy"] == "same-origin"
        assert "X-Next-Boundary" in response["Vary"]
        signal = reload.REFUSED_SIGNAL if status == 403 else None
        assert response.get(reload.RELOAD_HEADER) == signal

    @pytest.mark.parametrize(
        ("site", "crossed"),
        [(None, False), ("same-origin", False), ("same-site", True), ("none", True)],
    )
    def test_only_a_foreign_fetch_site_is_cross_site(
        self, site: str | None, *, crossed: bool
    ) -> None:
        headers = {} if site is None else {"HTTP_SEC_FETCH_SITE": site}
        assert reload.cross_site(RequestFactory().get("/", **headers)) is crossed


class TestRedeem:
    def test_the_project_settings_open_first(self, scripted) -> None:
        card = _info("card")
        claims = BoundaryClaims.of("card", "")
        backend = scripted({(): claims}, {("card", ""): card})
        assert _redeem() == (claims, card)
        assert backend.opened == [()]

    def test_an_expired_token_stops_at_once(self, scripted) -> None:
        backend = scripted(
            {(): _refusal(TokenRejection.EXPIRED)}, {("card", ""): _info("card")}
        )
        with pytest.raises(BoundaryTokenError):
            _redeem()
        assert backend.announced == [(TokenRejection.EXPIRED, "")]

    def test_a_component_override_opens_after_the_project_fails(self, scripted) -> None:
        bound = (("binding", "user"),)
        card = _info("card")
        claims = BoundaryClaims.of("card", "")
        backend = scripted(
            {(): _refusal(TokenRejection.SIGNATURE), bound: claims},
            {("card", ""): card},
            {"card": bound},
        )
        assert _redeem() == (claims, card)
        assert backend.opened == [(), bound]
        assert backend.announced == []

    def test_claims_of_a_component_the_page_lacks_are_refused(self, scripted) -> None:
        backend = scripted({(): BoundaryClaims.of("ghost", "")}, {})
        with pytest.raises(BoundaryTokenError) as caught:
            _redeem()
        assert caught.value.reason is TokenRejection.URL_MISMATCH
        assert backend.announced == [(TokenRejection.URL_MISMATCH, "ghost")]

    def test_claims_open_again_under_their_own_overrides(self, scripted) -> None:
        sized = (("max_bytes", 4096),)
        claims = BoundaryClaims.of("card", "")
        backend = scripted(
            {(): claims, sized: claims}, {("card", ""): _info("card")}, {"card": sized}
        )
        _redeem()
        assert backend.opened == [(), sized]

    def test_a_failed_second_opening_is_announced(self, scripted) -> None:
        sized = (("max_bytes", 8),)
        backend = scripted(
            {
                (): BoundaryClaims.of("card", ""),
                sized: _refusal(TokenRejection.TOO_LARGE),
            },
            {("card", ""): _info("card")},
            {"card": sized},
        )
        with pytest.raises(BoundaryTokenError):
            _redeem()
        assert backend.announced == [(TokenRejection.TOO_LARGE, "card")]

    def test_every_failed_opening_announces_the_first(self, scripted) -> None:
        bound = (("binding", "user"),)
        backend = scripted(
            {
                (): _refusal(TokenRejection.SIGNATURE),
                bound: _refusal(TokenRejection.BINDING),
            },
            {("card", ""): _info("card")},
            {"card": bound},
        )
        with pytest.raises(BoundaryTokenError) as caught:
            _redeem()
        assert caught.value.reason is TokenRejection.SIGNATURE
        assert backend.announced == [(TokenRejection.SIGNATURE, "")]


class TestAnswer:
    def test_props_of_another_type_are_refused_and_announced(
        self, scripted, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        claims = BoundaryClaims.of("card", "", props=[("n", "x")])
        backend = scripted({(): claims}, {("card", ""): _info("card")})
        monkeypatch.setattr(reload, "prop_types", lambda _info: {"n": int})
        monkeypatch.setattr(reload, "run_guards", lambda *_args: None)
        request = RequestFactory().get(
            "/home/", HTTP_X_NEXT_REQUEST="1", HTTP_X_NEXT_ZONE="b-0a0b0c0d-0"
        )
        response = reload.answer(request, _PATH, {}, "t", {})
        assert response.status_code == 403
        assert backend.announced == [(TokenRejection.MALFORMED, "card")]


class TestOpening:
    def test_a_refusal_no_override_lifts_never_reads_the_page(
        self, scripted, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = scripted({(): _refusal(TokenRejection.MALFORMED)}, {})

        def unread(_path: Path) -> dict:
            raise AssertionError

        monkeypatch.setattr(reload, "page_components", unread)
        with pytest.raises(BoundaryTokenError):
            _redeem()
        assert backend.opened == [()]
        assert backend.announced == [(TokenRejection.MALFORMED, "")]

    def test_an_override_set_that_finds_the_token_expired_stops(self, scripted) -> None:
        bound = (("binding", "user"),)
        backend = scripted(
            {
                (): _refusal(TokenRejection.SIGNATURE),
                bound: _refusal(TokenRejection.EXPIRED),
            },
            {("plain", ""): _info("plain"), ("card", ""): _info("card")},
            {"card": bound},
        )
        with pytest.raises(BoundaryTokenError) as caught:
            _redeem()
        assert caught.value.reason is TokenRejection.EXPIRED
        assert backend.opened == [(), bound]
        assert backend.announced == [(TokenRejection.EXPIRED, "")]


def _shown(name: str, directory: str) -> ComponentInfo:
    return ComponentInfo(
        name=name,
        scope_root=Path("/pages"),
        scope_relative="",
        template_path=Path(directory) / name / "component.djx",
        module_path=None,
        is_simple=True,
    )


class TestPageComponents:
    """The component set walks up to the page root and is held per stamp."""

    @pytest.fixture()
    def walked(self, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
        card = _shown("card", "/pages/_components")
        box = _info("box")
        seen: dict[Path, dict[str, ComponentInfo]] = {
            Path("/pages/home"): {"card": card},
            Path("/pages"): {"card": card, "box": box},
            Path("/pages/_components/card"): {"card": card},
        }
        visited: list[Path] = []

        def visible(directory: Path) -> dict[str, ComponentInfo]:
            visited.append(directory)
            return seen.get(directory, {})

        monkeypatch.setattr(reload, "_visible", visible)
        monkeypatch.setattr(reload, "page_tree_depth", lambda _start: 2)
        return visited

    def test_the_walk_stops_at_the_page_root(
        self, walked: list[Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reload, "_stamp", lambda: None)
        found = reload.page_components(Path("/pages/home/page.py"))
        assert sorted(name for name, _scope in found) == ["box", "card"]
        assert set(walked) == {
            Path("/pages/home"),
            Path("/pages"),
            Path("/pages/_components/card"),
        }

    def test_a_stamp_holds_the_set_until_it_moves(
        self, walked: list[Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stamp = ["one"]
        monkeypatch.setattr(reload, "_stamp", lambda: (stamp[0],))
        path = Path("/pages/home/held/page.py")
        first = reload.page_components(path)
        visits = len(walked)
        assert reload.page_components(path) is first
        assert len(walked) == visits
        stamp[0] = "two"
        reload.page_components(path)
        assert len(walked) == 2 * visits

    def test_no_stamp_walks_every_time(
        self, walked: list[Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(reload, "_stamp", lambda: None)
        path = Path("/pages/home/unheld/page.py")
        reload.page_components(path)
        visits = len(walked)
        reload.page_components(path)
        assert len(walked) == 2 * visits

    def test_watched_template_edits_leave_no_stamp(self) -> None:
        with override_settings(DEBUG=True):
            assert reload._stamp() is None
        with override_settings(DEBUG=False):
            stamp = reload._stamp()
        assert stamp is not None
        assert len(stamp) == 4

    def test_a_stamp_keeps_no_replaced_backend_alive(self) -> None:
        with override_settings(DEBUG=False):
            stamp = reload._stamp()
            held = weakref.ref(components_manager.backends[0])
            assert stamp == reload._stamp()
            components_manager.reset()
            gc.collect()
            assert held() is None
            assert stamp != reload._stamp()


class TestGuards:
    """The guard steps of a page run before its component, read once per graph."""

    @pytest.fixture()
    def ran(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
        held: list[tuple[str, object]] = []

        def sync(graph, path, phase, **kwargs) -> dict:
            held.append(("sync", phase.selected))
            return {}

        async def coloured(graph, path, phase, **kwargs) -> dict:
            held.append(("async", phase.selected))
            return {}

        monkeypatch.setattr(reload, "run_phase", sync)
        monkeypatch.setattr(reload, "arun_phase", coloured)
        return held

    def _graph(
        self, monkeypatch: pytest.MonkeyPatch, steps: frozenset[int], *, is_async: bool
    ) -> list[object]:
        graph = SimpleNamespace(steps=(SimpleNamespace(is_async=is_async),) * 2)
        monkeypatch.setattr(reload.page.contexts, "graph", lambda _path: graph)
        computed: list[object] = []

        def guards(read: object) -> frozenset[int]:
            computed.append(read)
            return steps

        monkeypatch.setattr(reload, "guard_steps", guards)
        return computed

    def _run(self, name: str) -> None:
        path = Path(f"/pages/{name}/page.py")
        reload.run_guards(path, RequestFactory().get("/"), {}, {})

    def test_a_page_without_a_guard_runs_nothing_and_reads_once(
        self, ran: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        computed = self._graph(monkeypatch, frozenset(), is_async=False)
        self._run("open")
        self._run("open")
        assert ran == []
        assert len(computed) == 1

    def test_a_sync_guard_runs_its_steps(
        self, ran: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._graph(monkeypatch, frozenset({0, 1}), is_async=False)
        self._run("sync")
        assert ran == [("sync", frozenset({0, 1}))]

    def test_an_async_guard_runs_through_the_bridge(
        self, ran: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._graph(monkeypatch, frozenset({0}), is_async=True)
        self._run("async")
        assert ran == [("async", frozenset({0}))]


class TestAdmits:
    """The rule checked before any prop decodes skips its object requirements."""

    _SOURCE = Path("/c/component.py")

    def _user(self, *, signed: bool, granted: bool) -> SimpleNamespace:
        return SimpleNamespace(is_authenticated=signed, has_perm=lambda *_args: granted)

    def test_an_open_rule_admits_anyone(self) -> None:
        assert reload._admits(None, OPEN)

    def test_a_signed_out_visitor_is_refused(self) -> None:
        rule = AccessRule((Requirement("permission", self._SOURCE, ("a.b",), "x"),))
        assert not reload._admits(self._user(signed=False, granted=True), rule)

    def test_a_missing_model_permission_is_refused(self) -> None:
        rule = AccessRule(
            (
                Requirement("login", self._SOURCE),
                Requirement("permission", self._SOURCE, ("a.b",)),
            )
        )
        assert not reload._admits(self._user(signed=True, granted=False), rule)

    def test_an_object_requirement_waits_for_the_props(self) -> None:
        rule = AccessRule((Requirement("permission", self._SOURCE, ("a.b",), "x"),))
        assert reload._admits(self._user(signed=True, granted=False), rule)


class TestEnvelope:
    def test_a_retry_carries_the_token_alone(self) -> None:
        failure = BoundaryError(ErrorKind.ERROR, "d", None, retry=True)
        response = reload._envelope(
            RequestFactory().get("/"),
            "b-0a0b0c0d-0",
            ("<b>x</b>", failure),
            StaticCollector(),
            "tok",
            (None, Path("page.py")),
        )
        (op,) = json.loads(response.content)["ops"]
        assert op[reload.RETRY_WITH] == "tok"
        assert "exp" not in op

    def test_a_bundle_the_content_needs_rides_the_envelope(self) -> None:
        collector = StaticCollector(
            bundle_url=lambda name: f"/static/next/next.{name}.min.js"
        )
        collector.require("more")
        response = reload._envelope(
            RequestFactory().get("/"),
            "b-0a0b0c0d-0",
            ("<a data-next-url>more</a>", None),
            collector,
            None,
            (None, Path("page.py")),
        )
        assert json.loads(response.content)["assets"] == [
            {"kind": "js", "url": "/static/next/next.more.min.js", "load": "script"}
        ]

    def test_no_input_mints_no_retry(self) -> None:
        assert reload._retry(RequestFactory().get("/"), None) is None

    def test_an_unencodable_prop_mints_no_retry(self) -> None:
        trip = TripInput(_info("card"), DeferMode.LOAD, {"sku": {"a": 1}})
        assert reload._retry(RequestFactory().get("/"), trip) is None

    def test_a_component_without_a_client_mode_reloads_as_load(self) -> None:
        assert reload._mode(_info("card")) is DeferMode.LOAD
