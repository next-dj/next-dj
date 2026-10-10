from django.test import RequestFactory

from next.consent import get_consent
from next.consent.backends import CookieConsentBackend
from next.consent.signals import consent_backend_loaded
from next.testing import capture_signals, override_next_settings


class TestConsentBackendLoadedSignal:
    """``consent_backend_loaded`` fires once per backend the manager loads."""

    def test_the_backend_loads_once_and_says_so(self) -> None:
        with (
            override_next_settings(CONSENT={}),
            capture_signals(consent_backend_loaded) as recorder,
        ):
            get_consent(RequestFactory().get("/"))
            get_consent(RequestFactory().get("/"))
        (event,) = recorder.events
        assert event.sender is CookieConsentBackend
        assert event.kwargs["config"] == {}
        assert isinstance(event.kwargs["instance"], CookieConsentBackend)
