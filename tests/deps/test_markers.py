from django.http import HttpRequest

from next.deps import (
    DependencyResolver,
    Depends,
    resolver,
)
from next.deps.markers import DependsProvider
from tests.support import (
    _ctx,
    _minimal_resolver,
    inspect_parameter,
)


class TestRegisterDependency:
    """Tests for register_dependency and dependency decorator (callable dependencies)."""

    def test_register_dependency_resolves_param_by_name(
        self, mock_http_request
    ) -> None:
        """A param whose name is a registered dependency gets the callable result."""

        def get_user(request: HttpRequest) -> str:
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = mock_http_request()

            def view(current_user: str = Depends("current_user")) -> str:
                return current_user

            cache: dict = {}
            stack: list[str] = []
            result = resolver.resolve_dependencies(
                view, request=request, _cache=cache, _stack=stack
            )
            assert result["current_user"] == "alice"
            assert view(**result) == "alice"
        finally:
            resolver._dependency_callables.pop("current_user", None)

    def test_dependency_decorator_registers_callable(self, mock_http_request) -> None:
        """@resolver.dependency('name') registers the function and returns it."""
        request = mock_http_request()

        @resolver.dependency("product")
        def get_product(request: HttpRequest, id: int) -> str:  # noqa: A002
            return f"product-{id}"

        def page(product: str = Depends("product")) -> str:
            return product

        try:
            cache: dict = {}
            stack: list[str] = []
            result = resolver.resolve_dependencies(
                page, request=request, id=3, _cache=cache, _stack=stack
            )
            assert result["product"] == "product-3"
        finally:
            resolver._dependency_callables.pop("product", None)

    def test_depends_callable_is_called_with_di_resolved_args(
        self, mock_http_request
    ) -> None:
        """Depends(callable) resolves callable args and calls it."""

        def build_value(request: HttpRequest, id: int) -> str:  # noqa: A002
            return f"{getattr(request, 'path', '')}:{id}"

        def view(value: str = Depends(build_value)) -> str:
            return value

        request = mock_http_request(path="/x/")
        resolved = resolver.resolve_dependencies(view, request=request, id=7)
        assert resolved["value"] == "/x/:7"
        assert view(**resolved) == "/x/:7"

    def test_depends_constant_value_injects_as_is(self) -> None:
        """Depends(value) injects a constant value when not str/callable."""

        def view(x: int = Depends(123)) -> int:
            return x

        r = DependencyResolver()
        resolved = r.resolve_dependencies(view)
        assert resolved["x"] == 123

    def test_depends_without_args_uses_param_name(self) -> None:
        """Depends() resolves by parameter name (Depends("param_name"))."""
        r = DependencyResolver()
        r.register_dependency("current_user", lambda: "alice")
        try:

            def view(current_user: str = Depends()) -> str:
                return current_user

            resolved = r.resolve_dependencies(view)
            assert resolved["current_user"] == "alice"
        finally:
            r._dependency_callables.pop("current_user", None)

    def test_unregistered_depends_name_returns_none(self) -> None:
        """When Depends("name") is used but name is not registered, param gets None."""

        def view(foo: object = Depends("nonexistent")) -> str:
            return str(foo)

        result = resolver.resolve_dependencies(view)
        assert result["foo"] is None

    def test_depends_provider_resolve_returns_none_when_default_not_depends(
        self,
    ) -> None:
        """Defensive: DependsProvider.resolve returns None when default isn't Depends."""
        provider = DependsProvider(DependencyResolver())
        param = inspect_parameter("x", int, default=123)
        ctx = _ctx()
        assert provider.resolve(param, ctx) is None

    def test_registered_dependency_not_used_if_url_kwargs_same_name(self) -> None:
        """URL kwargs take precedence: param 'id' gets url value, not a dependency."""
        r = _minimal_resolver()

        @r.dependency("id")
        def get_id() -> int:
            return 999

        def fn(id: object) -> object:  # noqa: A002
            return id

        cache: dict = {}
        result = r.resolve_dependencies(fn, id="from_url", _cache=cache)
        assert result["id"] == "from_url"  # dependency returning 999 is not used
