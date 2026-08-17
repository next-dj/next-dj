from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from next.urls import router_manager, urlpatterns


if TYPE_CHECKING:
    from next.urls.manager import _LazyUrlPatterns


@pytest.fixture(scope="module")
def lazy_patterns() -> _LazyUrlPatterns:
    """Return the sequence the mounted resolver reads, warmed to its cache."""
    patterns = urlpatterns[0].urlconf_name
    assert len(patterns) > 0
    return patterns


class TestBenchLazyUrlPatterns:
    """The pattern concat every resolve reads before dispatching."""

    @pytest.mark.benchmark(group="urls.lazy_patterns")
    def test_cached_concat(self, lazy_patterns: _LazyUrlPatterns, benchmark) -> None:
        benchmark(len, lazy_patterns)

    @pytest.mark.benchmark(group="urls.lazy_patterns")
    def test_version_token(self, lazy_patterns: _LazyUrlPatterns, benchmark) -> None:
        benchmark(lazy_patterns.version_token)


class TestBenchRouterManager:
    """Backend reads on the loaded manager, the state a resolve finds."""

    @pytest.mark.benchmark(group="urls.manager")
    def test_backends_snapshot(self, benchmark) -> None:
        assert router_manager.backends
        benchmark(lambda: router_manager.backends)

    @pytest.mark.benchmark(group="urls.manager")
    def test_iter_patterns(self, benchmark) -> None:
        benchmark(lambda: list(router_manager))
