import gc
import json
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404
from django.test import RequestFactory, override_settings

from next.boundaries.failures import BoundaryFailure, ErrorKind
from next.boundaries.options import DeferMode
from next.components import access as component_access
from next.components.info import ComponentInfo
from next.components.manager import components_manager
from next.components.trip import TripInput
from next.defer import BoundaryTokenError, TokenRejection, reload
from next.defer.tokens import BoundaryClaims
from next.defer.tokens.props import DEFAULT_LIMIT
from next.defer.tokens.settings import override_profile
from next.permissions.markers import OPEN, AccessRule, Requirement
from next.seeding import COLLECTOR_KEY
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
        self.unsettled: BoundaryTokenError | None = None
        self.named: str | None = None

    def profile(self, token):
        return self.named

    def open(self, request, token, *, tokens=()):
        self.opened.append(tokens)
        answer = self.answers[tokens]
        if isinstance(answer, BoundaryTokenError):
            raise answer
        return answer

    def settle(self, request, claims, target):
        if self.unsettled is not None:
            raise self.unsettled
        return claims

    def announce(self, request, error, component=""):
        self.announced.append((error.reason, component))

    def prop_limit(self, tokens=()) -> int:
        return DEFAULT_LIMIT


@pytest.fixture()
def scripted(monkeypatch: pytest.MonkeyPatch):
    def install(answers, candidates, overrides=None):
        backend = _Backend(answers)
        monkeypatch.setattr(reload.defer_manager, "tokens", lambda: backend)
        monkeypatch.setattr(reload, "page_components", lambda _path: candidates)
        held = overrides or {}
        monkeypatch.setattr(
            reload, "token_overrides", lambda info: held.get(info.name, ())
        )
        return backend

    return install


def _redeem(rf: RequestFactory):
    return reload.redeem(rf.get("/home/"), "t", _PATH, {})


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

    @pytest.mark.parametrize(
        ("answer", "reason"),
        [
            (reload.refused, TokenRejection.SIGNATURE),
            (reload.expired, TokenRejection.VERSION),
        ],
    )
    def test_a_token_refusal_names_no_reason_off_debug(self, answer, reason) -> None:
        response = answer(BoundaryTokenError(reason))
        assert response.content == b""
        assert reload.REJECTED_HEADER not in response

    @pytest.mark.parametrize(
        ("answer", "reason", "status"),
        [
            (reload.refused, TokenRejection.SIGNATURE, 403),
            (reload.expired, TokenRejection.VERSION, 409),
        ],
    )
    @override_settings(DEBUG=True)
    def test_a_token_refusal_names_its_reason_under_debug(
        self, answer, reason, status
    ) -> None:
        error = BoundaryTokenError(reason)
        response = answer(error)
        assert response.status_code == status
        assert response[reload.REJECTED_HEADER] == str(reason)
        assert response["Content-Type"].startswith("text/plain")
        assert error.detail in response.content.decode()
        assert response["Cache-Control"] == "private, no-store"

    @override_settings(DEBUG=True)
    def test_a_refusal_of_no_token_names_nothing_under_debug(self) -> None:
        response = reload.refused()
        assert response.content == b""
        assert reload.REJECTED_HEADER not in response

    def test_an_expired_token_asks_for_a_page_reload(self) -> None:
        response = reload.expired()
        assert response.status_code == 409
        assert response[reload.RELOAD_HEADER] == reload.EXPIRED_SIGNAL

    def test_a_reload_answers_reads_and_a_body_carrier_post(self) -> None:
        assert reload.methods() == ("GET", "HEAD")
        body = {"TOKENS": {"OPTIONS": {"CARRIER": "body"}}}
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
        self, rf, exc: Exception, status: int
    ) -> None:
        response = reload.failed(rf.get("/"), exc)
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
        self, rf, site: str | None, *, crossed: bool
    ) -> None:
        headers = {} if site is None else {"HTTP_SEC_FETCH_SITE": site}
        request = rf.get("/", **headers)
        assert reload.cross_site(request) is crossed
        assert "headers" not in vars(request)


class TestRedeem:
    """A backend that names no profile opens under the project, then the component."""

    def test_the_project_settings_open_first(self, rf, scripted) -> None:
        card = _info("card")
        claims = BoundaryClaims.of("card", "")
        backend = scripted({(): claims}, {("card", ""): card})
        assert _redeem(rf) == (claims, card)
        assert backend.opened == [()]

    def test_an_expired_token_stops_at_once(self, rf, scripted) -> None:
        backend = scripted(
            {(): _refusal(TokenRejection.EXPIRED)}, {("card", ""): _info("card")}
        )
        with pytest.raises(BoundaryTokenError):
            _redeem(rf)
        assert backend.announced == [(TokenRejection.EXPIRED, "")]
        assert backend.opened == [()]

    def test_claims_of_a_component_the_page_lacks_are_refused(
        self, rf, scripted
    ) -> None:
        backend = scripted({(): BoundaryClaims.of("ghost", "")}, {})
        with pytest.raises(BoundaryTokenError) as caught:
            _redeem(rf)
        assert caught.value.reason is TokenRejection.URL_MISMATCH
        assert backend.announced == [(TokenRejection.URL_MISMATCH, "ghost")]

    def test_claims_open_again_under_their_own_overrides(self, rf, scripted) -> None:
        sized = (("max_bytes", 4096),)
        claims = BoundaryClaims.of("card", "")
        backend = scripted(
            {(): claims, sized: claims}, {("card", ""): _info("card")}, {"card": sized}
        )
        _redeem(rf)
        assert backend.opened == [(), sized]

    def test_a_failed_second_opening_is_announced(self, rf, scripted) -> None:
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
            _redeem(rf)
        assert backend.announced == [(TokenRejection.TOO_LARGE, "card")]

    def test_a_settle_refusal_is_announced_with_the_component(
        self, rf, scripted
    ) -> None:
        backend = scripted(
            {(): BoundaryClaims.of("card", "")}, {("card", ""): _info("card")}
        )
        backend.unsettled = _refusal(TokenRejection.EXPIRED)
        with pytest.raises(BoundaryTokenError):
            _redeem(rf)
        assert backend.announced == [(TokenRejection.EXPIRED, "card")]


class TestNamedProfile:
    """A token that names its profile opens once, under that profile alone."""

    def test_the_project_profile_opens_once(self, rf, scripted) -> None:
        card = _info("card")
        claims = BoundaryClaims.of("card", "")
        backend = scripted({(): claims}, {("card", ""): card})
        backend.named = override_profile(())
        assert _redeem(rf) == (claims, card)
        assert backend.opened == [()]

    def test_a_named_profile_opens_under_the_overrides_it_names(
        self, rf, scripted
    ) -> None:
        bound = (("binding", "user"),)
        card = _info("card")
        claims = BoundaryClaims.of("card", "")
        backend = scripted(
            {bound: claims},
            {("plain", ""): _info("plain"), ("card", ""): card},
            {"card": bound},
        )
        backend.named = override_profile(bound)
        assert _redeem(rf) == (claims, card)
        assert backend.opened == [bound]
        assert backend.announced == []

    def test_a_profile_no_component_of_the_page_names_is_refused_unopened(
        self, rf, scripted
    ) -> None:
        backend = scripted({}, {("card", ""): _info("card")}, {"card": (("a", 1),)})
        backend.named = "f" * 12
        with pytest.raises(BoundaryTokenError) as caught:
            _redeem(rf)
        assert caught.value.reason is TokenRejection.URL_MISMATCH
        assert backend.opened == []
        assert backend.announced == [(TokenRejection.URL_MISMATCH, "")]

    def test_a_token_of_weaker_settings_never_reaches_a_stricter_component(
        self, rf, scripted
    ) -> None:
        bound = (("binding", "user"),)
        backend = scripted(
            {(): BoundaryClaims.of("card", "")},
            {("card", ""): _info("card")},
            {"card": bound},
        )
        backend.named = override_profile(())
        with pytest.raises(BoundaryTokenError) as caught:
            _redeem(rf)
        assert caught.value.reason is TokenRejection.URL_MISMATCH
        assert backend.opened == [()]
        assert backend.announced == [(TokenRejection.URL_MISMATCH, "card")]

    def test_a_refusal_of_the_named_opening_is_announced(self, rf, scripted) -> None:
        backend = scripted(
            {(): _refusal(TokenRejection.SIGNATURE)}, {("card", ""): _info("card")}
        )
        backend.named = override_profile(())
        with pytest.raises(BoundaryTokenError):
            _redeem(rf)
        assert backend.announced == [(TokenRejection.SIGNATURE, "")]


class TestAnswer:
    def test_props_of_another_type_are_refused_and_announced(
        self, rf, scripted, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        claims = BoundaryClaims.of("card", "", props=[("n", "x")])
        backend = scripted({(): claims}, {("card", ""): _info("card")})
        monkeypatch.setattr(reload, "prop_types", lambda _info: {"n": int})
        monkeypatch.setattr(reload, "run_guards", lambda *_args: None)
        request = rf.get(
            "/home/", HTTP_X_NEXT_REQUEST="1", HTTP_X_NEXT_ZONE="b-0a0b0c0d-0"
        )
        response = reload.answer(request, _PATH, {}, "t", {})
        assert response.status_code == 403
        assert backend.announced == [(TokenRejection.MALFORMED, "card")]


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

    @override_settings(DEBUG=True)
    def test_the_walk_stops_at_the_page_root(self, walked: list[Path]) -> None:
        found = reload.page_components(Path("/pages/home/page.py"))
        assert sorted(name for name, _scope in found) == ["box", "card"]
        assert set(walked) == {
            Path("/pages/home"),
            Path("/pages"),
            Path("/pages/_components/card"),
        }

    @override_settings(DEBUG=False)
    def test_a_set_is_held_until_the_components_move(self, walked: list[Path]) -> None:
        path = Path("/pages/home/held/page.py")
        first = reload.page_components(path)
        visits = len(walked)
        assert reload.page_components(path) is first
        assert len(walked) == visits
        components_manager.reset()
        reload.page_components(path)
        assert len(walked) == 2 * visits

    @override_settings(DEBUG=True)
    def test_watched_template_edits_walk_every_time(self, walked: list[Path]) -> None:
        path = Path("/pages/home/unheld/page.py")
        reload.page_components(path)
        visits = len(walked)
        reload.page_components(path)
        assert len(walked) == 2 * visits

    @override_settings(DEBUG=False)
    def test_a_held_set_keeps_no_replaced_backend_alive(
        self, walked: list[Path]
    ) -> None:
        reload.page_components(Path("/pages/home/weak/page.py"))
        held = weakref.ref(components_manager.backends[0])
        components_manager.reset()
        gc.collect()
        assert held() is None


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

    @staticmethod
    def _run(rf: RequestFactory, name: str) -> None:
        path = Path(f"/pages/{name}/page.py")
        reload.run_guards(path, rf.get("/"), {}, {})

    def test_a_page_without_a_guard_runs_nothing_and_reads_once(
        self, rf, ran: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        computed = self._graph(monkeypatch, frozenset(), is_async=False)
        self._run(rf, "open")
        self._run(rf, "open")
        assert ran == []
        assert len(computed) == 1

    def test_a_sync_guard_runs_its_steps(
        self, rf, ran: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._graph(monkeypatch, frozenset({0, 1}), is_async=False)
        self._run(rf, "sync")
        assert ran == [("sync", frozenset({0, 1}))]

    def test_an_async_guard_runs_through_the_bridge(
        self, rf, ran: list[tuple[str, object]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._graph(monkeypatch, frozenset({0}), is_async=True)
        self._run(rf, "async")
        assert ran == [("async", frozenset({0}))]


class _Isolated:
    """A component renderer answering fixed markup, noting the bundle it needs."""

    def __init__(
        self, rendered: tuple[str, BoundaryFailure | None], retry: TripInput | None
    ) -> None:
        self.rendered = rendered
        self.retry = retry
        self.needs: str | None = None

    def body(self, context):
        if self.needs is not None:
            context[COLLECTOR_KEY].require(self.needs)
        return self.rendered

    def error(self, context, kind):
        return "<b>e</b>", BoundaryFailure(kind, "d", None, retry=False)

    def retry_input(self, failure) -> TripInput | None:
        return self.retry


@pytest.fixture()
def reloaded(rf, scripted, monkeypatch: pytest.MonkeyPatch):
    """Return a function answering a reload of `card` with the page seams scripted."""

    def answer(
        *,
        module: Path | None = None,
        rule: AccessRule = OPEN,
        user: object = None,
        rendered: tuple[str, BoundaryFailure | None] = ("<b>x</b>", None),
        retry: TripInput | None = None,
        needs: str | None = None,
    ) -> SimpleNamespace:
        scripted(
            {(): BoundaryClaims.of("card", "")}, {("card", ""): _info("card", module)}
        )
        monkeypatch.setattr(reload, "prop_types", lambda _info: {})
        monkeypatch.setattr(reload, "run_guards", lambda *_args: None)
        monkeypatch.setattr(component_access, "declared", lambda _module: rule)
        held = SimpleNamespace(trip=None, response=None)
        isolated = _Isolated(rendered, retry)
        isolated.needs = needs

        def renderer(info, path, trip):
            held.trip = trip
            return isolated

        monkeypatch.setattr(reload, "isolated_renderer", renderer)
        request = rf.get(
            "/home/", HTTP_X_NEXT_REQUEST="1", HTTP_X_NEXT_ZONE="b-0a0b0c0d-0"
        )
        request.user = user
        held.response = reload.answer(request, _PATH, {}, "t", {})
        return held

    return answer


def _ops(held: SimpleNamespace) -> list[dict]:
    return json.loads(held.response.content)["ops"]


class TestClears:
    """The rule checked before any prop decodes skips its object requirements."""

    _SOURCE = Path("/c/component.py")

    def _user(self, *, signed: bool, granted: bool) -> SimpleNamespace:
        return SimpleNamespace(is_authenticated=signed, has_perm=lambda *_args: granted)

    def test_an_open_rule_admits_anyone(self, reloaded) -> None:
        assert reloaded().response.status_code == 200

    def test_a_signed_out_visitor_is_refused(self, reloaded) -> None:
        rule = AccessRule((Requirement("permission", self._SOURCE, ("a.b",), "x"),))
        user = self._user(signed=False, granted=True)
        held = reloaded(module=self._SOURCE, rule=rule, user=user)
        assert held.response.status_code == 403

    def test_a_missing_model_permission_is_refused(self, reloaded) -> None:
        rule = AccessRule(
            (
                Requirement("login", self._SOURCE),
                Requirement("permission", self._SOURCE, ("a.b",)),
            )
        )
        user = self._user(signed=True, granted=False)
        held = reloaded(module=self._SOURCE, rule=rule, user=user)
        assert held.response.status_code == 403

    def test_an_object_requirement_waits_for_the_props(
        self, reloaded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(component_access, "check", lambda *_args: None)
        rule = AccessRule((Requirement("permission", self._SOURCE, ("a.b",), "x"),))
        user = self._user(signed=True, granted=False)
        held = reloaded(module=self._SOURCE, rule=rule, user=user)
        assert held.response.status_code == 200


class TestEnvelope:
    def test_a_retry_carries_the_token_alone(
        self, reloaded, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=True)
        trip = TripInput(_info("card"), DeferMode.LOAD, {})
        monkeypatch.setattr(reload, "mint", lambda _request, _trip: "tok")
        held = reloaded(rendered=("<b>x</b>", failure), retry=trip)
        (op,) = _ops(held)
        assert op[reload.RETRY_WITH] == "tok"
        assert "exp" not in op

    def test_a_bundle_the_content_needs_rides_the_envelope(self, reloaded) -> None:
        held = reloaded(rendered=("<a data-next-url>more</a>", None), needs="more")
        assets = json.loads(held.response.content)["assets"]
        assert [asset["kind"] for asset in assets] == ["js"]
        assert assets[0]["url"].endswith("next.more.min.js")

    def test_no_input_mints_no_retry(self, reloaded) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=True)
        held = reloaded(rendered=("<b>x</b>", failure), retry=None)
        (op,) = _ops(held)
        assert reload.RETRY_WITH not in op

    def test_an_unencodable_prop_mints_no_retry(self, reloaded) -> None:
        failure = BoundaryFailure(ErrorKind.ERROR, "d", None, retry=True)
        trip = TripInput(_info("card"), DeferMode.LOAD, {"sku": {"a": 1}})
        held = reloaded(rendered=("<b>x</b>", failure), retry=trip)
        (op,) = _ops(held)
        assert reload.RETRY_WITH not in op

    def test_a_component_without_a_client_mode_reloads_as_load(self, reloaded) -> None:
        assert reloaded().trip.mode is DeferMode.LOAD
