import logging
import re

import pytest
from django.http import Http404
from django.test import override_settings

from next.consent import (
    NECESSARY,
    UNDECIDED,
    Consent,
    ConsentBackend,
    consent_categories,
    get_consent,
)
from next.consent.manager import (
    CONSENT_ATTR,
    consent_backend_manager,
    consent_configured,
    consent_payload,
    server_mode,
    server_render,
)
from next.pages.responses import mark_shared_render, shared_render
from next.testing import override_next_settings
from tests.support import consent_request


class HeaderConsentBackend(ConsentBackend):
    """A backend that reads no cookie the runtime writes."""

    def read(self, request):
        return UNDECIDED


class BrokenInitBackend(ConsentBackend):
    """A backend that cannot be built."""

    def __init__(self, config: object = None) -> None:
        """Refuse to be built."""
        msg = "no init"
        raise RuntimeError(msg)

    def read(self, request):  # pragma: no cover - never built
        return UNDECIDED


class RaisingReadBackend(ConsentBackend):
    """A backend whose read raises."""

    def read(self, request):
        msg = "no read"
        raise RuntimeError(msg)


class NotFoundBackend(ConsentBackend):
    """A backend that answers 404 on purpose."""

    def read(self, request):
        raise Http404


class WrongTypeBackend(ConsentBackend):
    """A backend that answers no `Consent`."""

    def read(self, request):
        return {"analytics": True}


class RaisingConfigBackend(ConsentBackend):
    """A backend whose runtime entries raise."""

    def read(self, request):
        return UNDECIDED

    def client_config(self):
        msg = "no config"
        raise RuntimeError(msg)


class WrongConfigBackend(ConsentBackend):
    """A backend whose runtime entries are no mapping, and try to shadow the choice."""

    def read(self, request):
        return UNDECIDED

    def client_config(self):
        return ["cookie"]


class ShadowingConfigBackend(ConsentBackend):
    """A backend naming an entry of its own and one the choice owns."""

    def read(self, request):
        return Consent(frozenset({NECESSARY}), decided=True)

    def client_config(self):
        return {"endpoint": "/consent/", "decided": False}


# The `storedChoice` pattern of `consent.ts`, matched whole as JS `$` ends the string.
CLIENT_CHOICE = re.compile(r"([12]):([^:]*):[^:]*")
LOGGER = "next.consent.manager"


class TestSettings:
    """The `CONSENT` scope is read leniently, the checks reporting what it drops."""

    def test_the_defaults(self) -> None:
        assert consent_categories() == ("necessary",)
        assert server_render() is None
        assert not consent_configured()

    def test_necessary_leads_whatever_the_list(self) -> None:
        with override_next_settings(CONSENT={"CATEGORIES": ["ads", 3, "", "ads"]}):
            assert consent_categories() == ("necessary", "ads")
            assert consent_configured()

    def test_a_reload_moves_whether_it_is_configured(self) -> None:
        assert not consent_configured()
        with override_next_settings(CONSENT={}):
            assert consent_configured()
        assert not consent_configured()

    def test_a_list_of_the_wrong_shape_keeps_necessary_alone(self) -> None:
        with override_next_settings(CONSENT={"CATEGORIES": "ads"}):
            assert consent_categories() == ("necessary",)

    @pytest.mark.parametrize("mode", [True, False])
    def test_the_render_mode(self, mode) -> None:
        with override_next_settings(CONSENT={"SERVER_RENDER": mode}):
            assert server_render() is mode

    def test_an_unusable_mode_reads_as_auto(self) -> None:
        with override_next_settings(CONSENT={"SERVER_RENDER": "yes"}):
            assert server_render() is None

    def test_a_scope_without_the_mode_reads_the_default(self) -> None:
        with override_next_settings(CONSENT={"CATEGORIES": ["analytics"]}):
            assert server_render() is None


class TestGetConsent:
    """The consent of a request is read once and kept to the configured categories."""

    def test_anything_but_a_request_is_undecided(self) -> None:
        assert get_consent(None) == UNDECIDED

    def test_the_cookie_is_read_and_held(self) -> None:
        request = consent_request("1:analytics,unknown:1")
        with override_next_settings(CONSENT={"CATEGORIES": ["analytics"]}):
            consent = get_consent(request)
        assert consent == Consent(frozenset({NECESSARY, "analytics"}), decided=True)
        request.COOKIES["next_consent"] = "1::1"
        assert get_consent(request) is consent
        assert getattr(request, CONSENT_ATTR) is consent


class TestClientParity:
    """The server reads a cookie exactly as `storedChoice` in `consent.ts` does."""

    @pytest.mark.parametrize(
        "value",
        [
            "1:analytics:1700",
            "1:analytics,marketing:1",
            "2:analytics|marketing:1",
            "2:analytics,marketing:1",
            "2::1",
            "2:|analytics|:1",
            "1:analytics|marketing:1",
            "1::1",
            "1:necessary:1",
            "1:analytics:",
            "1:,,analytics,,:1",
            "1:,:1",
            "1: analytics :1",
            "1:analytics ,marketing:1",
            "1:Analytics:1",
            "1:ANALYTICS,marketing:1",
            "1:unknown,analytics:1",
            "1:analytics:1:extra",
            "1:analytics:1700:",
            "1:a:b:c:d",
            "1:analytics",
            "1",
            " 1:analytics:1",
            "1 :analytics:1",
            "01:analytics:1",
            "2:analytics:1",
            "3:analytics:1",
            "02:analytics:1",
            "2:analytics",
            "::",
            ":",
            "",
        ],
    )
    def test_the_cookie_reads_alike_on_both_sides(self, value: str) -> None:
        match = CLIENT_CHOICE.fullmatch(value)
        consent = get_consent(consent_request(value))
        if match is None:
            assert consent == UNDECIDED
            return
        categories = consent_categories()
        separator = "," if match.group(1) == "1" else "|"
        listed = match.group(2).split(separator)
        granted = {name for name in listed if name in categories}
        assert consent == Consent(frozenset({NECESSARY, *granted}), decided=True)


class TestPayload:
    """The `$consent` entry carries the cookie, the categories and the choice."""

    def test_the_entry_of_a_decided_visitor(self) -> None:
        consent = Consent(frozenset({NECESSARY, "marketing"}), decided=True)
        with override_next_settings(CONSENT={"CATEGORIES": ["marketing", "ads"]}):
            payload = consent_payload(consent)
        assert payload["categories"] == ["necessary", "marketing", "ads"]
        assert payload["decided"] is True
        assert payload["granted"] == ["necessary", "marketing"]
        assert payload["cookie"] == consent_backend_manager.get().cookie()
        assert "cookies" not in payload

    def test_a_backend_without_the_cookie_names_none(self) -> None:
        backend = f"{__name__}.HeaderConsentBackend"
        with override_next_settings(CONSENT={"BACKEND": backend}):
            payload = consent_payload(UNDECIDED)
        assert "cookie" not in payload
        assert payload["decided"] is False


def _consent_of(backend: type, *, debug: bool = False):
    path = f"{__name__}.{backend.__name__}"
    with (
        override_next_settings(CONSENT={"BACKEND": path, "CATEGORIES": ["ads"]}),
        override_settings(DEBUG=debug),
    ):
        return [get_consent(consent_request("2:ads:1")) for _ in range(2)]


class TestFailingBackend:
    """A backend that fails denies every category but necessary, logged once."""

    @pytest.mark.parametrize(
        ("backend", "fragment"),
        [
            (BrokenInitBackend, "failed to load"),
            (RaisingReadBackend, "RaisingReadBackend.read() of"),
            (WrongTypeBackend, "answered 'dict', not a next.consent.Consent"),
        ],
    )
    def test_production_reads_undecided_and_logs_once(
        self, backend: type, fragment: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, LOGGER):
            assert _consent_of(backend) == [UNDECIDED, UNDECIDED]
        assert len(caplog.records) == 1
        assert fragment in caplog.text
        assert "NEXT_FRAMEWORK['CONSENT']['BACKEND']" in caplog.text

    def test_an_unimportable_path_reads_undecided(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            override_next_settings(CONSENT={"BACKEND": "no.such.Backend"}),
            caplog.at_level(logging.ERROR, LOGGER),
        ):
            assert get_consent(consent_request("2::1")) == UNDECIDED
            assert "cookie" not in consent_payload(UNDECIDED)
        assert caplog.text.count("failed to load") == 1

    @pytest.mark.parametrize(
        ("backend", "error"),
        [
            (BrokenInitBackend, RuntimeError),
            (RaisingReadBackend, RuntimeError),
            (WrongTypeBackend, TypeError),
        ],
    )
    def test_debug_raises_naming_the_setting(self, backend: type, error) -> None:
        with pytest.raises(error) as caught:
            _consent_of(backend, debug=True)
        text = str(caught.value) + "".join(getattr(caught.value, "__notes__", []))
        assert "NEXT_FRAMEWORK['CONSENT']['BACKEND']" in text

    def test_an_intended_answer_passes(self) -> None:
        with pytest.raises(Http404):
            _consent_of(NotFoundBackend)


class TestClientConfig:
    """`$consent` carries what the backend adds, the choice winning a clash."""

    def _payload(self, backend: type, *, debug: bool = False):
        path = f"{__name__}.{backend.__name__}"
        with (
            override_next_settings(CONSENT={"BACKEND": path}),
            override_settings(DEBUG=debug),
        ):
            return [consent_payload(UNDECIDED) for _ in range(2)]

    def test_a_backend_entry_joins_the_choice(self) -> None:
        payload, _again = self._payload(ShadowingConfigBackend)
        assert payload["endpoint"] == "/consent/"
        assert payload["decided"] is False

    @pytest.mark.parametrize(
        ("backend", "fragment"),
        [(RaisingConfigBackend, "raised"), (WrongConfigBackend, "not a mapping")],
    )
    def test_a_failing_backend_entry_adds_nothing_and_logs_once(
        self, backend: type, fragment: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, LOGGER):
            payloads = self._payload(backend)
        assert all(
            set(payload) == {"categories", "decided", "granted"} for payload in payloads
        )
        assert len(caplog.records) == 1
        assert fragment in caplog.text

    def test_a_raising_backend_entry_raises_under_debug(self) -> None:
        with pytest.raises(RuntimeError, match="no config"):
            self._payload(RaisingConfigBackend, debug=True)


class TestRenderMode:
    """A render follows the cookie unless a shared cache or the setting says no."""

    def test_the_mode_follows_the_setting_and_the_request(self) -> None:
        request = consent_request()
        assert server_mode(None)
        assert server_mode(request)
        mark_shared_render(request)
        assert shared_render(request)
        assert not server_mode(request)
        with override_next_settings(CONSENT={"SERVER_RENDER": True}):
            assert server_mode(request)
        with override_next_settings(CONSENT={"SERVER_RENDER": False}):
            assert not server_mode(consent_request())
