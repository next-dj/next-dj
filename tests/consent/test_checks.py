import pytest
from django.test import override_settings

from next.consent.checks import (
    check_consent_categories,
    check_consent_cookie_secure,
    check_consent_settings,
)
from tests.support import check_ids


class TestConsentSettings:
    """`check_consent_settings` reads the raw `CONSENT` scope."""

    @pytest.mark.parametrize(
        "framework",
        [
            {},
            {"CONSENT": "x"},
            {"CONSENT": {}},
            {
                "CONSENT": {
                    "BACKEND": "next.consent.CookieConsentBackend",
                    "CATEGORIES": ["necessary", "ads"],
                    "SERVER_RENDER": False,
                    "OPTIONS": {"cookie_name": "c"},
                }
            },
        ],
        ids=["absent", "non_dict", "empty", "full"],
    )
    def test_a_usable_scope_is_silent(self, framework: dict[str, object]) -> None:
        with override_settings(NEXT_FRAMEWORK=framework):
            assert check_consent_settings() == []

    def test_an_unknown_key_is_e035(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"CONSENT": {"COOKIE": "x"}}):
            messages = check_consent_settings()
        assert check_ids(messages) == ["next.E035"]
        assert "NEXT_FRAMEWORK['CONSENT']" in messages[0].msg

    @pytest.mark.parametrize(
        ("backend", "fragment"),
        [
            (3, "is no dotted path"),
            ("no.such.Backend", "does not import"),
            ("next.consent.Consent", "is no next.consent.ConsentBackend subclass"),
        ],
    )
    def test_an_unusable_backend_is_e137(self, backend: object, fragment: str) -> None:
        with override_settings(NEXT_FRAMEWORK={"CONSENT": {"BACKEND": backend}}):
            messages = check_consent_settings()
        assert check_ids(messages) == ["next.E137"]
        assert fragment in messages[0].msg
        assert "remove BACKEND" in messages[0].msg

    def test_an_unknown_render_mode_is_e145(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"CONSENT": {"SERVER_RENDER": "yes"}}):
            messages = check_consent_settings()
        assert check_ids(messages) == ["next.E145"]
        assert "'SERVER_RENDER'" in messages[0].msg


class TestConsentCategories:
    """A usable category list (`next.E135`) of names the cookie carries (`E146`)."""

    @pytest.mark.parametrize(
        ("categories", "fragment"),
        [
            (["analytics"], "does not list 'necessary'"),
            ("x", "lists no category"),
            (["necessary", ""], "lists no category"),
        ],
    )
    def test_an_unusable_list_is_e135(self, categories: object, fragment: str) -> None:
        with override_settings(NEXT_FRAMEWORK={"CONSENT": {"CATEGORIES": categories}}):
            messages = check_consent_categories()
        assert check_ids(messages) == ["next.E135"]
        assert fragment in messages[0].msg

    def test_a_name_the_cookie_cannot_carry_is_e146(self) -> None:
        listed = [
            "necessary",
            "ad_storage",
            "web-v2.1",
            "a,b",
            "a:b",
            "a|b",
            "a;b",
            "a b",
            "é",
        ]
        with override_settings(NEXT_FRAMEWORK={"CONSENT": {"CATEGORIES": listed}}):
            messages = check_consent_categories()
        assert check_ids(messages) == ["next.E146"] * 6
        assert "'a,b'" in messages[0].msg
        assert "'|'" in messages[0].msg
        assert "letters, digits" in messages[0].msg

    @pytest.mark.parametrize(
        "framework",
        [{}, {"CONSENT": {}}, {"CONSENT": {"CATEGORIES": ["necessary", "ads"]}}],
    )
    def test_a_usable_list_is_silent(self, framework: dict[str, object]) -> None:
        with override_settings(NEXT_FRAMEWORK=framework):
            assert check_consent_categories() == []


class TestConsentCookieSecure:
    """A deploy with a Secure session cookie keeps the consent cookie Secure too."""

    def test_an_explicit_insecure_cookie_is_w129(self) -> None:
        with override_settings(
            SESSION_COOKIE_SECURE=True,
            NEXT_FRAMEWORK={"CONSENT": {"OPTIONS": {"secure": False}}},
        ):
            assert check_ids(check_consent_cookie_secure()) == ["next.W129"]

    @pytest.mark.parametrize(
        ("session", "consent"),
        [
            (True, {"OPTIONS": {"secure": None}}),
            (True, {}),
            (False, {"OPTIONS": {"secure": False}}),
        ],
    )
    def test_anything_else_is_silent(self, session, consent) -> None:
        with override_settings(
            SESSION_COOKIE_SECURE=session, NEXT_FRAMEWORK={"CONSENT": consent}
        ):
            assert check_consent_cookie_secure() == []
