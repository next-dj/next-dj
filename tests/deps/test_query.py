from unittest.mock import MagicMock

import pytest
from django.http import HttpRequest, QueryDict

from next.deps import DependencyResolver
from next.urls import DQuery, QueryParamProvider, get_multi_values
from next.urls.parser import _coerce_url_value
from tests.support import _ctx, inspect_parameter


@pytest.fixture()
def provider() -> QueryParamProvider:
    """Return a fresh `QueryParamProvider` instance for each test."""
    return QueryParamProvider()


@pytest.fixture()
def make_request():
    """Return a builder that produces an `HttpRequest` mock with a populated `GET`."""

    def _build(query_string: str = "") -> HttpRequest:
        request = MagicMock(spec=HttpRequest)
        request.GET = QueryDict(query_string)
        return request

    return _build


@pytest.fixture()
def request_ctx(make_request):
    """Return a builder that wraps a request inside a `ResolutionContext` stand-in."""

    def _build(query_string: str = ""):
        return _ctx(request=make_request(query_string))

    return _build


class TestQueryParamProviderCanHandle:
    """Cover the `can_handle` predicate of the query provider."""

    @pytest.mark.parametrize(
        ("annotation", "request_present", "expected"),
        [
            (DQuery[str], True, True),
            (DQuery[int], True, True),
            (DQuery[list[str]], True, True),
            (DQuery[str], False, False),
            (str, True, False),
            (int, True, False),
            (list[str], True, False),
        ],
    )
    def test_can_handle_matrix(
        self,
        provider,
        make_request,
        annotation,
        request_present,
        expected,
    ) -> None:
        """Match `DQuery[...]` annotations only when a request is attached."""
        request = make_request() if request_present else None
        ctx = _ctx(request=request)
        param = inspect_parameter("q", annotation)
        assert provider.can_handle(param, ctx) is expected


class TestQueryParamProviderResolveScalar:
    """Cover scalar resolution and type coercion."""

    @pytest.mark.parametrize(
        ("query", "annotation", "expected"),
        [
            ("q=hello", DQuery[str], "hello"),
            ("page=3", DQuery[int], 3),
            ("active=true", DQuery[bool], True),
            ("active=1", DQuery[bool], True),
            ("active=yes", DQuery[bool], True),
            ("active=no", DQuery[bool], False),
            ("active=0", DQuery[bool], False),
            ("ratio=1.5", DQuery[float], 1.5),
            ("page=abc", DQuery[int], "abc"),
            ("ratio=oops", DQuery[float], "oops"),
        ],
    )
    def test_scalar_value(
        self,
        provider,
        request_ctx,
        query,
        annotation,
        expected,
    ) -> None:
        """Return a coerced value when the key is present in the query string."""
        ctx = request_ctx(query)
        param_name = query.split("=", 1)[0]
        param = inspect_parameter(param_name, annotation)
        assert provider.resolve(param, ctx) == expected

    def test_missing_key_with_default(self, provider, request_ctx) -> None:
        """Return the parameter default when the query key is absent."""
        ctx = request_ctx()
        param = inspect_parameter("q", DQuery[str], default="empty")
        assert provider.resolve(param, ctx) == "empty"

    def test_missing_key_without_default(self, provider, request_ctx) -> None:
        """Return `None` when the query key is absent and no default is declared."""
        ctx = request_ctx()
        param = inspect_parameter("q", DQuery[str])
        assert provider.resolve(param, ctx) is None

    def test_missing_request_with_default(self, provider) -> None:
        """Return the parameter default when no request is attached to the context."""
        ctx = _ctx(request=None)
        param = inspect_parameter("q", DQuery[str], default="fallback")
        assert provider.resolve(param, ctx) == "fallback"

    def test_missing_request_without_default(self, provider) -> None:
        """Return `None` when no request is attached and no default is declared."""
        ctx = _ctx(request=None)
        param = inspect_parameter("q", DQuery[str])
        assert provider.resolve(param, ctx) is None

    def test_unparametrised_dquery_falls_back_to_string(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Treat a bare `DQuery` annotation as `DQuery[str]`."""
        ctx = request_ctx("q=hello")
        param = inspect_parameter("q", DQuery)
        assert provider.resolve(param, ctx) == "hello"

    def test_non_type_inner_hint_treated_as_string(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Coerce as `str` when the inner hint is a string literal rather than a type."""
        ctx = request_ctx("q=hello")
        param = inspect_parameter("q", DQuery["ignored"])
        assert provider.resolve(param, ctx) == "hello"


class TestQueryParamProviderResolveMultiValue:
    """Cover multi-value resolution including the bracket-suffix variant."""

    @pytest.mark.parametrize(
        ("query", "annotation", "expected"),
        [
            ("brand=Acme&brand=Globex", DQuery[list[str]], ["Acme", "Globex"]),
            ("brand=Acme", DQuery[list[str]], ["Acme"]),
            ("ids=1&ids=2&ids=3", DQuery[list[int]], [1, 2, 3]),
            ("brand[]=Acme&brand[]=Globex", DQuery[list[str]], ["Acme", "Globex"]),
            ("ids[]=1&ids[]=2", DQuery[list[int]], [1, 2]),
            ("brand=Acme,Globex", DQuery[list[str]], ["Acme", "Globex"]),
            ("ids=1,2,3", DQuery[list[int]], [1, 2, 3]),
            ("brand=Acme,,Globex,", DQuery[list[str]], ["Acme", "Globex"]),
        ],
    )
    def test_multi_value(
        self,
        provider,
        request_ctx,
        query,
        annotation,
        expected,
    ) -> None:
        """Collect repeated keys from plain, bracket, and comma-delimited forms."""
        ctx = request_ctx(query)
        param = inspect_parameter(query.split("=", 1)[0].rstrip("[]"), annotation)
        assert provider.resolve(param, ctx) == expected

    def test_repeated_plain_form_skips_comma_split(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Treat each repeated value as atomic when the plain form has multiple entries."""
        ctx = request_ctx("brand=Acme,Inc&brand=Globex")
        param = inspect_parameter("brand", DQuery[list[str]])
        assert provider.resolve(param, ctx) == ["Acme,Inc", "Globex"]

    def test_plain_single_value_with_bracket_present(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Keep the bracket form when the plain form has a single entry that is empty."""
        ctx = request_ctx("brand=&brand[]=Acme&brand[]=Globex")
        param = inspect_parameter("brand", DQuery[list[str]])
        assert provider.resolve(param, ctx) == ["Acme", "Globex"]

    def test_comma_split_only_when_single_plain_value(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Split on commas only when exactly one plain value is present."""
        ctx = request_ctx("brand=Acme,Globex")
        param = inspect_parameter("brand", DQuery[list[str]])
        assert provider.resolve(param, ctx) == ["Acme", "Globex"]

    def test_empty_list_when_key_absent_no_default(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Return an empty list when neither plain nor bracket key is present."""
        ctx = request_ctx()
        param = inspect_parameter("brand", DQuery[list[str]])
        assert provider.resolve(param, ctx) == []

    def test_empty_returns_default_when_provided(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Return the declared default when the multi-value key is absent."""
        ctx = request_ctx()
        param = inspect_parameter(
            "brand",
            DQuery[list[str]],
            default=("fallback",),
        )
        assert provider.resolve(param, ctx) == ("fallback",)

    def test_nested_list_hint_falls_back_to_string_coercion(
        self,
        provider,
        request_ctx,
    ) -> None:
        """Coerce list elements as strings when the inner hint is a generic alias."""
        ctx = request_ctx("brand=Acme&brand=Globex")
        param = inspect_parameter("brand", DQuery[list[list[str]]])
        assert provider.resolve(param, ctx) == ["Acme", "Globex"]


class TestQueryParamProviderEndToEnd:
    """Drive the provider through a real `DependencyResolver`."""

    def test_resolves_through_dependency_resolver(self, make_request) -> None:
        """Resolve scalar and multi-value query parameters end to end."""
        resolver = DependencyResolver(QueryParamProvider())
        request = make_request("q=hello&page=2&brand=Acme&brand=Globex")

        def view(
            q: DQuery[str] = "",
            page: DQuery[int] = 1,
            brand: DQuery[list[str]] = (),
        ) -> tuple[str, int, list[str]]:
            return q, page, list(brand)

        resolved = resolver.resolve_dependencies(view, request=request)
        assert resolved["q"] == "hello"
        assert resolved["page"] == 2
        assert resolved["brand"] == ["Acme", "Globex"]
        assert view(**resolved) == ("hello", 2, ["Acme", "Globex"])

    def test_resolves_qs_bracket_form(self, make_request) -> None:
        """Resolve multi-value params written in the qs-style bracket syntax."""
        resolver = DependencyResolver(QueryParamProvider())
        request = make_request("brand[]=Acme&brand[]=Globex")

        def view(brand: DQuery[list[str]] = ()) -> list[str]:
            return list(brand)

        resolved = resolver.resolve_dependencies(view, request=request)
        assert resolved["brand"] == ["Acme", "Globex"]


class TestCoerceUrlValue:
    """Cover the shared string coercion helper used by URL and query parsers."""

    @pytest.mark.parametrize(
        ("value", "hint", "expected"),
        [
            ("42", int, 42),
            ("oops", int, "oops"),
            ("1.5", float, 1.5),
            ("oops", float, "oops"),
            ("true", bool, True),
            ("yes", bool, True),
            ("1", bool, True),
            ("no", bool, False),
            ("hello", str, "hello"),
        ],
    )
    def test_coerce(self, value, hint, expected) -> None:
        """Coerce a raw string to the requested primitive type when possible."""
        assert _coerce_url_value(value, hint) == expected


class TestGetMultiValues:
    """Cover the public `get_multi_values` helper."""

    def _request(self, query_string: str) -> HttpRequest:
        request = MagicMock(spec=HttpRequest)
        request.GET = QueryDict(query_string)
        return request

    @pytest.mark.parametrize(
        ("query_string", "expected"),
        [
            ("brand=Acme&brand=Globex", ["Acme", "Globex"]),
            ("brand[]=Acme&brand[]=Globex", ["Acme", "Globex"]),
            ("brand=Acme,Globex", ["Acme", "Globex"]),
            ("brand=Acme", ["Acme"]),
            ("", []),
        ],
    )
    def test_wire_formats(self, query_string, expected) -> None:
        """Return all values across plain-repeated, bracket, and comma forms."""
        assert get_multi_values(self._request(query_string), "brand") == expected

    def test_absent_key_returns_empty(self) -> None:
        """Return an empty list when the key is not present at all."""
        assert get_multi_values(self._request("other=x"), "brand") == []
