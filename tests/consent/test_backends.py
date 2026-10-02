import pytest

from next.consent import (
    NECESSARY,
    UNDECIDED,
    Consent,
    ConsentBackend,
    CookieConsentBackend,
)
from tests.support import consent_request, cookie_request


class _Plain(ConsentBackend):
    def read(self, request):
        return UNDECIDED


class TestConsentBackend:
    """The base backend holds its entry."""

    def test_the_options_come_from_the_entry(self) -> None:
        assert _Plain({"OPTIONS": {"x": 1}}).options == {"x": 1}

    @pytest.mark.parametrize("config", [None, {}, {"OPTIONS": "x"}])
    def test_an_entry_without_options_has_none(self, config) -> None:
        assert _Plain(config).options == {}


class TestCookieConsentBackend:
    """The runtime writes `1:<categories>:<seconds>`, the backend reads it back."""

    def test_a_request_without_the_cookie_is_undecided(self) -> None:
        assert CookieConsentBackend().read(consent_request()) == UNDECIDED

    def test_the_granted_categories_are_read(self) -> None:
        read = CookieConsentBackend().read(
            consent_request("1:analytics,marketing:1700")
        )
        assert read == Consent(
            frozenset({NECESSARY, "analytics", "marketing"}), decided=True
        )

    def test_a_choice_of_nothing_is_decided(self) -> None:
        read = CookieConsentBackend().read(consent_request("1::1700"))
        assert read == Consent(frozenset({NECESSARY}), decided=True)

    @pytest.mark.parametrize("value", ["2:analytics:1", "analytics", "1:a", ""])
    def test_a_foreign_value_is_undecided(self, value: str) -> None:
        assert CookieConsentBackend().read(consent_request(value)) == UNDECIDED

    def test_the_cookie_name_is_an_option(self) -> None:
        backend = CookieConsentBackend({"OPTIONS": {"cookie_name": "c"}})
        assert backend.cookie_name == "c"
        assert backend.read(cookie_request(c="1:analytics:1")).allows("analytics")

    def test_an_unusable_cookie_name_falls_back(self) -> None:
        backend = CookieConsentBackend({"OPTIONS": {"cookie_name": 3}})
        assert backend.cookie_name == "next_consent"

    def test_the_cookie_is_described_for_the_runtime(self) -> None:
        assert CookieConsentBackend().cookie() == {
            "name": "next_consent",
            "max_age": 15552000,
            "samesite": "Lax",
            "secure": None,
            "domain": None,
            "path": "/",
        }

    def test_the_cookie_keeps_usable_options_only(self) -> None:
        options = {
            "max_age": True,
            "secure": True,
            "domain": ".acme.example",
            "path": "",
            "samesite": "Strict",
        }
        cookie = CookieConsentBackend({"OPTIONS": options}).cookie()
        assert cookie == {
            "name": "next_consent",
            "max_age": 15552000,
            "samesite": "Strict",
            "secure": True,
            "domain": ".acme.example",
            "path": "/",
        }

    def test_a_samesite_of_none_leaves_the_attribute_out(self) -> None:
        cookie = CookieConsentBackend({"OPTIONS": {"samesite": None}}).cookie()
        assert cookie["samesite"] is None
