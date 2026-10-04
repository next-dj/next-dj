import pytest

from next.consent import get_consent
from tests.support import consent_request


class TestBenchGetConsent:
    """The consent read every page with gated scripts or a consent tag pays once."""

    @pytest.mark.parametrize(
        "cookie",
        [None, "1:analytics,marketing:1700", "2:foreign"],
        ids=["undecided", "granted", "foreign"],
    )
    @pytest.mark.benchmark(group="consent.manager")
    def test_first_read(self, benchmark, cookie: str | None) -> None:
        """The cookie parsed and kept to the configured categories."""
        benchmark.pedantic(
            get_consent, setup=lambda: ((consent_request(cookie),), {}), rounds=2000
        )

    @pytest.mark.benchmark(group="consent.manager")
    def test_held_read(self, benchmark) -> None:
        """A later read of the same request, answered from the request."""
        request = consent_request("1:analytics,marketing:1700")
        get_consent(request)
        benchmark(get_consent, request)
