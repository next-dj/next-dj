import functools
from pathlib import Path

import pytest

from next.introspect import (
    callable_name,
    declared_file,
    defining_file,
    registering_file,
)
from next.pages.loaders import _load_python_module
from tests.support import attribution, unwrapped_decorator, wraps_decorator


class TestDefiningFile:
    """``defining_file`` unwraps decorators and partials to the declaring file."""

    def test_plain_function_returns_its_own_file(self) -> None:
        """A function declared here is attributed to this test module."""

        def handler() -> None:
            pass

        assert defining_file(handler) == Path(__file__)

    def test_function_wrapped_with_functools_wraps_returns_user_file(self) -> None:
        """A wrapper built with ``functools.wraps`` is unwrapped to the user file."""

        @wraps_decorator
        def handler() -> None:
            pass

        assert defining_file(handler) == Path(__file__)

    def test_function_wrapped_without_functools_wraps_returns_wrapper_file(
        self,
    ) -> None:
        """Without ``functools.wraps`` the wrapper's own file wins."""

        @unwrapped_decorator
        def handler() -> None:
            pass

        assert defining_file(handler) == Path(attribution.__file__)

    def test_class_returns_the_file_it_was_declared_in(self) -> None:
        """A class is attributed to the file declaring it."""

        class Marker:
            pass

        assert defining_file(Marker) == Path(__file__)

    def test_partial_returns_the_file_of_the_wrapped_function(self) -> None:
        """A ``functools.partial`` is attributed to the function it binds."""

        def handler(value: int) -> int:
            return value

        assert defining_file(functools.partial(handler, 1)) == Path(__file__)

    def test_nested_partial_unwraps_to_the_innermost_function(self) -> None:
        """Stacked partials resolve through to the declaring file."""

        def handler(first: int, second: int) -> int:
            return first + second

        stacked = functools.partial(functools.partial(handler, 1), 2)
        assert defining_file(stacked) == Path(__file__)

    def test_partial_of_a_builtin_raises_type_error(self) -> None:
        """A partial binding a builtin has no declaring file either."""
        with pytest.raises(TypeError, match=r"could not determine the file where"):
            defining_file(functools.partial(len))

    def test_callable_instance_returns_the_file_of_its_class(self) -> None:
        """An instance with ``__call__`` is attributed through that method."""

        class Handler:
            def __call__(self) -> None:
                pass

        assert defining_file(Handler()) == Path(__file__)

    def test_callable_without_code_object_raises_type_error(self) -> None:
        """A builtin has no ``__code__`` and raises ``TypeError``."""
        with pytest.raises(TypeError, match=r"could not determine the file where"):
            defining_file(len)

    def test_non_callable_object_raises_type_error_naming_the_object(self) -> None:
        """A non-callable value raises ``TypeError`` naming the value."""
        with pytest.raises(TypeError, match=r"'not a callable'"):
            defining_file("not a callable")

    def test_wrapper_loop_falls_back_to_the_outer_function(self, tmp_path) -> None:
        """A ``__wrapped__`` cycle answers with the file of the outer wrapper."""
        module_file = tmp_path / "page.py"
        module_file.write_text("def inner() -> None:\n    pass\n")
        module = _load_python_module(module_file)

        def outer() -> None:
            pass

        outer.__wrapped__ = module.inner
        module.inner.__wrapped__ = outer

        assert defining_file(outer) == Path(__file__)

    @pytest.mark.parametrize("member", ["__call__", "__init__"], ids=["call", "init"])
    def test_class_without_a_registered_module_reads_its_own_body(
        self, tmp_path, member
    ) -> None:
        """A class the loader execs without sys.modules answers through its body."""
        module_file = tmp_path / "page.py"
        module_file.write_text(
            f"class Declared:\n    def {member}(self) -> None:\n        pass\n"
        )
        module = _load_python_module(module_file)

        assert defining_file(module.Declared) == module_file

    def test_class_without_a_module_or_body_raises_type_error(self, tmp_path) -> None:
        """A body-less class outside sys.modules names no file at all."""
        module_file = tmp_path / "page.py"
        module_file.write_text("class Declared:\n    marker = 1\n")
        module = _load_python_module(module_file)

        with pytest.raises(TypeError, match=r"could not determine the file where"):
            defining_file(module.Declared)


class TestRegistrationSite:
    """A decorator factory learns the file calling it and the file declaring `func`."""

    def test_registering_file_skips_the_factory_frame(self, tmp_path: Path) -> None:
        factory_file = tmp_path / "factory.py"
        factory_file.write_text("def factory(read):\n    return read()\n")
        module = _load_python_module(factory_file)
        assert module is not None
        assert module.factory(registering_file) == Path(__file__)

    def test_a_callable_declared_where_it_registers_notes_nothing(self) -> None:
        notes: list[tuple[object, ...]] = []

        def handler() -> None:
            pass

        declared = declared_file(handler, Path(__file__), lambda *n: notes.append(n))
        assert declared == Path(__file__)
        assert notes == []

    def test_a_callable_declared_elsewhere_is_noted(self, tmp_path: Path) -> None:
        notes: list[tuple[object, ...]] = []
        running = tmp_path / "page.py"

        def handler() -> None:
            pass

        declared = declared_file(handler, running, lambda *n: notes.append(n))
        assert declared == Path(__file__)
        assert notes == [(running, Path(__file__), handler)]


class TestCallableName:
    """``callable_name`` names a callable through a partial or a bound instance."""

    def test_function_keeps_its_own_name(self) -> None:
        """A plain function reports ``__name__``."""

        def handler() -> None:
            pass

        assert callable_name(handler) == "handler"

    def test_partial_reports_the_wrapped_function(self) -> None:
        """A partial has no ``__name__``, so the bound function names it."""

        def handler(value: int) -> int:
            return value

        stacked = functools.partial(functools.partial(handler, 1))
        assert callable_name(stacked) == "handler"

    def test_callable_instance_reports_its_class(self) -> None:
        """An instance with ``__call__`` reports the class name."""

        class Handler:
            def __call__(self) -> None:
                pass

        assert callable_name(Handler()) == "Handler"
