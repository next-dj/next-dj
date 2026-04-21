from __future__ import annotations

from django.http import HttpRequest

from next.deps import Depends, resolver
from next.deps.cache import _IN_PROGRESS, DependencyCache


class TestDependencyCache:
    """Direct tests for DependencyCache (get sentinel, len, contains)."""

    def test_get_returns_in_progress_when_key_marked(self) -> None:
        """Get returns _IN_PROGRESS while key is in the in-progress set."""
        cache = DependencyCache()
        cache.mark_in_progress("dep")
        assert cache.get("dep") is _IN_PROGRESS
        cache.set("dep", "done")
        assert cache.get("dep") == "done"

    def test_len_and_contains_use_backing_cache(self) -> None:
        """__len__ and __contain__ reflect stored values, not in-progress markers."""
        cache = DependencyCache()
        assert len(cache) == 0
        assert "x" not in cache
        cache.set("x", 1)
        assert len(cache) == 1
        assert "x" in cache

    def test_is_in_progress_and_unmark(self) -> None:
        """The ``is_in_progress`` flag reflects ``mark``. ``unmark`` clears it."""
        cache = DependencyCache()
        cache.mark_in_progress("k")
        assert cache.is_in_progress("k")
        cache.unmark_in_progress("k")
        assert not cache.is_in_progress("k")


class TestCallableDependencyCache:
    """Tests for request-scoped caching of dependency callable results."""

    def test_same_dependency_requested_twice_called_once_with_cache(
        self, mock_http_request
    ) -> None:
        """Two resolve_dependencies calls sharing _cache: dependency callable runs once."""
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = mock_http_request()

            def view1(current_user: str = Depends("current_user")) -> str:
                return current_user

            def view2(current_user: str = Depends("current_user")) -> str:
                return current_user

            cache: dict = {}
            stack: list[str] = []
            result1 = resolver.resolve_dependencies(
                view1, request=request, _cache=cache, _stack=stack
            )
            result2 = resolver.resolve_dependencies(
                view2, request=request, _cache=cache, _stack=stack
            )
            assert result1["current_user"] == "alice"
            assert result2["current_user"] == "alice"
            assert call_count == 1
            assert cache.get("current_user") == "alice"
        finally:
            resolver._dependency_callables.pop("current_user", None)

    def test_without_cache_dependency_called_each_resolve(
        self, mock_http_request
    ) -> None:
        """Without _cache, each resolve_dependencies call invokes the dependency."""
        call_count = 0

        def get_user(request: HttpRequest) -> str:
            nonlocal call_count
            call_count += 1
            return "alice"

        resolver.register_dependency("current_user", get_user)
        try:
            request = mock_http_request()

            def view(current_user: str = Depends("current_user")) -> str:
                return current_user

            resolver.resolve_dependencies(view, request=request)
            resolver.resolve_dependencies(view, request=request)
            assert call_count == 2
        finally:
            resolver._dependency_callables.pop("current_user", None)
