import pytest
from django.test import RequestFactory

from next.csrf import csrf_view


FLAG = {"HTTP_X_NEXT_REQUEST": "1", "HTTP_SEC_FETCH_SITE": "same-origin"}


class TestBenchCsrfView:
    """The token endpoint the runtime of every shared page calls before a post."""

    @pytest.mark.benchmark(group="csrf.view")
    def test_a_token(self, benchmark) -> None:
        """A flagged same-origin read, minting a masked token and every header."""
        request = RequestFactory().get("/_next/csrf/", **FLAG)
        csrf_view(request)
        benchmark(csrf_view, request)

    @pytest.mark.parametrize(
        "headers",
        [{}, {**FLAG, "HTTP_SEC_FETCH_SITE": "cross-site"}],
        ids=["unflagged", "cross-site"],
    )
    @pytest.mark.benchmark(group="csrf.view")
    def test_a_refusal(self, benchmark, headers: dict[str, str]) -> None:
        """A read the endpoint turns away, which mints nothing."""
        request = RequestFactory().get("/_next/csrf/", **headers)
        benchmark(csrf_view, request)
