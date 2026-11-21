import pytest
from django.http import HttpRequest

from next.deps import RegisteredParameterProvider
from next.urls.markers import (
    DQuery,
    DUrl,
    HttpRequestProvider,
    QueryParamProvider,
    UrlByAnnotationProvider,
    UrlKwargsProvider,
)
from tests.support import inspect_parameter


class TestUrlProvidersNeverBlock:
    """Every URL provider reads the request or its kwargs in memory."""

    @pytest.mark.parametrize(
        ("provider", "param"),
        [
            pytest.param(
                HttpRequestProvider(),
                inspect_parameter("request", HttpRequest),
                id="request",
            ),
            pytest.param(
                UrlByAnnotationProvider(),
                inspect_parameter("pk", DUrl[int]),
                id="url-annotation",
            ),
            pytest.param(
                UrlKwargsProvider(), inspect_parameter("slug", str), id="url-kwargs"
            ),
            pytest.param(
                QueryParamProvider(), inspect_parameter("page", DQuery[int]), id="query"
            ),
        ],
    )
    def test_static_blocking_is_false(
        self, provider: RegisteredParameterProvider, param
    ) -> None:
        """The async resolver fills the parameter on the loop, never in a thread."""
        assert provider.static_blocking(param) is False
