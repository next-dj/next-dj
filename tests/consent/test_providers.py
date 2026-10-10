from unittest.mock import MagicMock

from django.test import RequestFactory

from next.consent import NECESSARY, UNDECIDED, Consent
from next.consent.providers import ConsentProvider
from next.deps import DependencyResolver
from next.pages.responses import cookie_varies, mark_shared_render
from next.testing import override_next_settings
from tests.support import inspect_parameter


ANALYTICS = {"CATEGORIES": ["analytics"]}


def _handler(consent: Consent) -> Consent:
    return consent


class TestConsentProvider:
    """A parameter annotated `Consent` receives the consent of the request."""

    def test_the_request_consent_is_injected_and_the_response_varies(self) -> None:
        request = RequestFactory().get("/")
        request.COOKIES["next_consent"] = "1:analytics:1"
        with override_next_settings(CONSENT=ANALYTICS):
            resolved = DependencyResolver().resolve_dependencies(
                _handler, request=request
            )
        assert resolved["consent"] == Consent(
            frozenset({NECESSARY, "analytics"}), decided=True
        )
        assert cookie_varies(request)

    def test_a_shared_render_sees_an_undecided_visitor(self) -> None:
        request = RequestFactory().get("/")
        request.COOKIES["next_consent"] = "1:analytics:1"
        mark_shared_render(request)
        resolved = DependencyResolver().resolve_dependencies(_handler, request=request)
        assert resolved["consent"] == UNDECIDED
        assert not cookie_varies(request)
        with override_next_settings(CONSENT={**ANALYTICS, "SERVER_RENDER": True}):
            resolved = DependencyResolver().resolve_dependencies(
                _handler, request=request
            )
        assert resolved["consent"].allows("analytics")

    def test_a_server_kept_off_the_cookie_sees_an_undecided_visitor(self) -> None:
        request = RequestFactory().get("/")
        request.COOKIES["next_consent"] = "1:analytics:1"
        with override_next_settings(CONSENT={"SERVER_RENDER": False}):
            resolved = DependencyResolver().resolve_dependencies(
                _handler, request=request
            )
        assert resolved["consent"] == UNDECIDED
        assert not cookie_varies(request)

    def test_without_a_request_the_visitor_is_undecided(self) -> None:
        resolved = DependencyResolver().resolve_dependencies(_handler, request=None)
        assert resolved["consent"] == UNDECIDED

    def test_the_provider_settles_on_the_annotation(self) -> None:
        provider = ConsentProvider()
        claimed = inspect_parameter("consent", Consent)
        other = inspect_parameter("consent", str)
        assert provider.static_can_handle(claimed) is True
        assert provider.static_can_handle(other) is False
        assert provider.can_handle(other, MagicMock()) is False
