from next.deps.cache import DependencyCache
from next.testing import make_resolution_context


class TestResolutionContext:
    """The per-call snapshot exposes its fields and defaults after construction."""

    def test_fields_and_defaults_are_readable(self) -> None:
        cache = DependencyCache()
        context = make_resolution_context(cache=cache)
        assert context.request is None
        assert context.form is None
        assert context.url_kwargs == {}
        assert context.context_data == {}
        assert context.cache is cache
        assert context.stack == []
        assert context.cleaned_data is None

    def test_each_context_owns_a_fresh_stack(self) -> None:
        first = make_resolution_context()
        second = make_resolution_context()
        first.stack.append("theme")
        assert first.stack is not second.stack
        assert second.stack == []

    def test_equality_is_identity(self) -> None:
        cache = DependencyCache()
        first = make_resolution_context(cache=cache)
        second = make_resolution_context(cache=cache)
        assert first != second
