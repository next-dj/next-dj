from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import pytest
from django.http import HttpResponse
from django.urls import Resolver404, URLResolver, path
from django.urls.resolvers import RoutePattern

from next.urls import TrieURLResolver


if TYPE_CHECKING:
    from django.urls import URLPattern


_SECTIONS = 10
_PAGES = 12


def _bench_view(_request, **kwargs) -> HttpResponse:
    return HttpResponse(b"bench")


def _build_patterns() -> list[URLPattern]:
    patterns = [
        path(f"sec{i}/page{j}/", _bench_view, name=f"bench_s{i}_p{j}")
        for i in range(_SECTIONS)
        for j in range(_PAGES)
    ]
    patterns.append(
        path("deep/<slug:one>/mid/<slug:two>/leaf/", _bench_view, name="bench_deep")
    )
    patterns.append(path("items/<int:item_id>/", _bench_view, name="bench_item"))
    patterns.append(path("files/<path:rest>/", _bench_view, name="bench_files"))
    return patterns


_MOUNT_PREFIX = "wallets/<int:wallet>/"


def _build_mounted_patterns() -> list[URLPattern]:
    """Return a tree under a parametrised segment in each route, then one beside it."""
    mounted = [
        path(
            f"{_MOUNT_PREFIX}sec{i}/page{j}/",
            _bench_view,
            name=f"bench_wallets_int_wallet_s{i}_p{j}",
        )
        for i in range(_SECTIONS)
        for j in range(_PAGES)
    ]
    return [*mounted, *_build_patterns()]


@pytest.fixture(scope="module")
def mounted_resolver() -> URLResolver:
    """Return one trie over a tree under a parametrised segment and one beside it."""
    built = TrieURLResolver(RoutePattern(""), _build_mounted_patterns())
    with contextlib.suppress(Resolver404):
        built.resolve("warmup/miss/")
    return built


@pytest.fixture(scope="module")
def included_resolver() -> URLResolver:
    """Return a trie included under a prefix with a parameter, as `pages()` mounts."""
    tree = TrieURLResolver(RoutePattern(""), _build_patterns())
    built = URLResolver(
        RoutePattern("", is_endpoint=False),
        [path("org/<int:org>/audit/", ([tree], "audit", "audit"))],
    )
    with contextlib.suppress(Resolver404):
        built.resolve("warmup/miss/")
    return built


@pytest.fixture(scope="module")
def route_patterns() -> list[URLPattern]:
    return _build_patterns()


@pytest.fixture(scope="module", params=["trie", "linear"])
def resolver(request, route_patterns: list[URLPattern]) -> URLResolver:
    resolver_cls = TrieURLResolver if request.param == "trie" else URLResolver
    built = resolver_cls(RoutePattern(""), route_patterns)
    with contextlib.suppress(Resolver404):
        built.resolve("warmup/miss/")
    return built


class TestBenchResolverModes:
    @pytest.mark.benchmark(group="urls.resolver")
    def test_static_hit_last_route(self, resolver: URLResolver, benchmark) -> None:
        benchmark(resolver.resolve, "sec9/page11/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_dynamic_hit_int_converter(self, resolver: URLResolver, benchmark) -> None:
        benchmark(resolver.resolve, "items/12345/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_deep_dynamic_path(self, resolver: URLResolver, benchmark) -> None:
        benchmark(resolver.resolve, "deep/alpha/mid/beta/leaf/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_path_tail_hit(self, resolver: URLResolver, benchmark) -> None:
        benchmark(resolver.resolve, "files/docs/guide/intro.txt/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_miss_raises_resolver404(self, resolver: URLResolver, benchmark) -> None:
        def run() -> None:
            with contextlib.suppress(Resolver404):
                resolver.resolve("missing/route/")

        benchmark(run)


class TestBenchMountedResolve:
    """A route under a parametrised segment against one beside it, through the trie."""

    @pytest.mark.benchmark(group="urls.resolver")
    def test_mounted_prefix_hit(self, mounted_resolver: URLResolver, benchmark) -> None:
        benchmark(mounted_resolver.resolve, "wallets/42/sec9/page11/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_unprefixed_hit_beside_mount(
        self, mounted_resolver: URLResolver, benchmark
    ) -> None:
        benchmark(mounted_resolver.resolve, "sec9/page11/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_unprefixed_dynamic_hit_beside_mount(
        self, mounted_resolver: URLResolver, benchmark
    ) -> None:
        benchmark(mounted_resolver.resolve, "items/12345/")

    @pytest.mark.benchmark(group="urls.resolver")
    def test_included_tree_static_hit(
        self, included_resolver: URLResolver, benchmark
    ) -> None:
        benchmark(included_resolver.resolve, "org/7/audit/sec9/page11/")
