import functools

import pytest
from django.http import HttpRequest

from next.deps import DependencyResolver, Depends, UnknownDependencyError, resolver
from next.deps.markers import DependsProvider
from next.testing import make_resolution_context
from tests.support import _ctx, _minimal_resolver, bound_dependency, inspect_parameter


class TestRegisterDependency:
    """Tests for register_dependency and dependency decorator (callable dependencies)."""

    def test_register_dependency_resolves_param_by_name(
        self, mock_http_request
    ) -> None:
        """A param whose name is a registered dependency gets the callable result."""

        def get_user(request: HttpRequest) -> str:
            return "alice"

        def view(current_user: str = Depends("current_user")) -> str:
            return current_user

        request = mock_http_request()
        cache: dict = {}
        stack: list[str] = []
        with bound_dependency("current_user", get_user):
            result = resolver.resolve_dependencies(
                view, request=request, _cache=cache, _stack=stack
            )
        assert result["current_user"] == "alice"
        assert view(**result) == "alice"

    def test_dependency_decorator_registers_callable(self, mock_http_request) -> None:
        """@dependency('name') registers the function and returns it."""
        request = mock_http_request()
        r = DependencyResolver()

        @r.dependency("product")
        def get_product(request: HttpRequest, obj_id: int) -> str:
            return f"product-{obj_id}"

        def page(product: str = Depends("product")) -> str:
            return product

        assert r.get_dependency("product") is get_product
        cache: dict = {}
        stack: list[str] = []
        result = r.resolve_dependencies(
            page, request=request, obj_id=3, _cache=cache, _stack=stack
        )
        assert result["product"] == "product-3"

    def test_depends_callable_is_called_with_di_resolved_args(
        self, mock_http_request
    ) -> None:
        """Depends(callable) resolves callable args and calls it."""

        def build_value(request: HttpRequest, obj_id: int) -> str:
            return f"{getattr(request, 'path', '')}:{obj_id}"

        def view(value: str = Depends(build_value)) -> str:
            return value

        request = mock_http_request(path="/x/")
        resolved = resolver.resolve_dependencies(view, request=request, obj_id=7)
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

        def view(current_user: str = Depends()) -> str:
            return current_user

        r = DependencyResolver()
        r.register_dependency("current_user", lambda: "alice")
        resolved = r.resolve_dependencies(view)
        assert resolved["current_user"] == "alice"

    def test_depends_provider_resolve_returns_none_when_default_not_depends(
        self,
    ) -> None:
        """``DependsProvider.resolve`` yields ``None`` for a non-``Depends`` default."""
        provider = DependsProvider(DependencyResolver())
        param = inspect_parameter("x", int, default=123)
        ctx = _ctx()
        assert provider.resolve(param, ctx) is None

    def test_registered_dependency_not_used_if_url_kwargs_same_name(self) -> None:
        """URL kwargs take precedence so param 'obj_id' gets the url value, not a dependency."""
        r = _minimal_resolver()

        @r.dependency("obj_id")
        def get_obj_id() -> int:
            return 999

        def fn(obj_id: object) -> object:
            return obj_id

        cache: dict = {}
        result = r.resolve_dependencies(fn, obj_id="from_url", _cache=cache)
        assert result["obj_id"] == "from_url"  # dependency returning 999 is not used


class TestUnknownDependency:
    """The string branch of `Depends` fails fast on a name nothing registered."""

    def test_unregistered_depends_name_raises(self) -> None:
        def view(foo: object = Depends("nonexistent")) -> str:
            return str(foo)

        with pytest.raises(UnknownDependencyError) as exc_info:
            resolver.resolve_dependencies(view)
        error = exc_info.value
        assert isinstance(error, LookupError)
        assert error.name == "nonexistent"
        assert error.param_name == "foo"
        message = str(error)
        assert 'Depends("nonexistent") on parameter "foo"' in message
        assert f'of "{view.__name__}" ({__file__})' in message
        assert 'resolver.dependency("nonexistent")' in message

    def test_bare_depends_raises_for_an_unregistered_parameter_name(self) -> None:
        def view(nonexistent: object = Depends()) -> str:
            return str(nonexistent)

        with pytest.raises(UnknownDependencyError) as exc_info:
            resolver.resolve_dependencies(view)
        message = str(exc_info.value)
        assert 'Depends("nonexistent") on parameter "nonexistent"' in message
        assert __file__ in message

    def test_depends_callable_and_constant_are_untouched(self) -> None:
        def factory() -> str:
            return "built"

        def view(built: str = Depends(factory), const: int = Depends(123)) -> None:
            return None

        assert resolver.resolve_dependencies(view) == {"built": "built", "const": 123}

    def test_uncovered_parameters_keep_the_default_or_none(self) -> None:
        def view(plain: object, with_default: int = 7) -> None:
            return None

        assert resolver.resolve_dependencies(view) == {"plain": None, "with_default": 7}

    def test_bound_method_is_named_by_its_function(self) -> None:
        class Holder:
            def method(self, theme: str = Depends("theme")) -> str:
                return theme

        with pytest.raises(UnknownDependencyError) as exc_info:
            resolver.resolve_dependencies(Holder().method)
        message = str(exc_info.value)
        assert f'of "{Holder.method.__name__}" ({__file__})' in message

    def test_partial_is_named_by_the_callable_it_wraps(self) -> None:
        def view(theme: str = Depends("theme")) -> str:
            return theme

        partial = functools.partial(view)
        with pytest.raises(UnknownDependencyError) as exc_info:
            resolver.resolve_dependencies(partial)
        assert f'of "{view.__name__}" names' in str(exc_info.value)

    def test_class_callable_is_named_without_a_path(self) -> None:
        class Service:
            def __init__(self, theme: str = Depends("theme")) -> None:
                self.theme = theme

        with pytest.raises(UnknownDependencyError) as exc_info:
            resolver.resolve_dependencies(Service)
        message = str(exc_info.value)
        assert f'of "{Service.__name__}" names' in message
        assert __file__ not in message

    def test_registered_name_resolves_again(self) -> None:
        def view(theme: str = Depends("theme")) -> str:
            return theme

        with pytest.raises(UnknownDependencyError):
            resolver.resolve_dependencies(view)
        with bound_dependency("theme", lambda: "dark"):
            assert resolver.resolve_dependencies(view) == {"theme": "dark"}
        with pytest.raises(UnknownDependencyError):
            resolver.resolve_dependencies(view)

    def test_close_registered_name_is_suggested(self) -> None:
        def view(theme: str = Depends("tehme")) -> str:
            return theme

        with (
            bound_dependency("theme", lambda: "dark"),
            pytest.raises(UnknownDependencyError) as exc_info,
        ):
            resolver.resolve_dependencies(view)
        error = exc_info.value
        assert error.suggestion == "theme"
        message = str(error)
        assert message.endswith('Did you mean "theme"?')
        assert "fix the name" not in message

    def test_empty_registry_gives_no_suggestion(self) -> None:
        def view(theme: str = Depends("tehme")) -> str:
            return theme

        with pytest.raises(UnknownDependencyError) as exc_info:
            DependencyResolver().resolve_dependencies(view)
        error = exc_info.value
        assert error.suggestion is None
        message = str(error)
        assert "Did you mean" not in message
        assert message.endswith(
            'Register it with resolver.dependency("tehme") or fix the name.'
        )


def _built(theme: str = Depends("theme")) -> str:
    return f"built-{theme}"


class TestDependsFormsTakeBothPaths:
    """Every `Depends` form answers the same through `resolve` and through the filler."""

    def _provider(self) -> DependsProvider:
        instance = DependencyResolver()
        instance.dependency("theme")(lambda: "dark")
        return DependsProvider(resolver=instance)

    @pytest.mark.parametrize(
        ("dependency", "name", "expected"),
        [
            ("theme", "value", "dark"),
            (None, "theme", "dark"),
            (_built, "value", "built-dark"),
            (123, "value", 123),
        ],
        ids=["named", "bare", "callable", "constant"],
    )
    def test_resolve_and_compiled_filler_agree(
        self, dependency, name, expected
    ) -> None:
        provider = self._provider()
        param = inspect_parameter(name, default=Depends(dependency))
        fill = provider.compile_resolve(param)
        assert fill is not None
        assert provider.resolve(param, make_resolution_context()) == expected
        assert fill(make_resolution_context()) == expected

    def test_resolve_ignores_a_default_that_is_no_marker(self) -> None:
        param = inspect_parameter("value", default="plain")
        assert self._provider().resolve(param, make_resolution_context()) is None

    @pytest.mark.parametrize("dependency", ["missing", None], ids=["named", "bare"])
    def test_an_unregistered_name_raises_through_both_paths(self, dependency) -> None:
        provider = self._provider()
        param = inspect_parameter("missing", default=Depends(dependency))
        fill = provider.compile_resolve(param)
        assert fill is not None
        for call in (
            lambda: provider.resolve(param, make_resolution_context()),
            lambda: fill(make_resolution_context()),
        ):
            with pytest.raises(UnknownDependencyError) as exc_info:
                call()
            assert exc_info.value.name == "missing"
            assert exc_info.value.param_name == "missing"
