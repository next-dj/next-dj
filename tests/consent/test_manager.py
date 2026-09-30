import re

import pytest

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


# The `storedChoice` pattern of `consent.ts`, matched whole as JS `$` ends the string.
CLIENT_CHOICE = re.compile(r"1:([^:]*):[^:]*")


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
        granted = {name for name in match.group(1).split(",") if name in categories}
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
