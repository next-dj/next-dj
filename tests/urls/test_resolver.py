import uuid
from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import override_settings
from django.urls import Resolver404, URLResolver, include, path, re_path, reverse
from django.urls.resolvers import RoutePattern

from next.forms.uid import URL_NAME_FORM_ACTION
from next.testing import override_form_action, override_next_settings
from next.urls import TrieURLResolver, page_reverse, router_manager
from next.urls.manager import urlpatterns
from tests.support import default_page_router_config


_TREE_ROUTES = (
    "",
    "home",
    "blog/2024/post",
    "items/[int:id]",
    "tag/[slug:tag]",
    "u/[uuid:uid]",
    "name/[username]",
    "files/[[rest]]",
    "docs/[[chapter]]/end",
    "articles/latest",
    "articles/[slug:topic]",
    "num/[int:x]",
    "num/[str:x]",
)

_RESOLVE_CASES = (
    ("", "page_", {}),
    ("home/", "page_home", {}),
    ("blog/2024/post/", "page_blog_2024_post", {}),
    ("items/9/", "page_items_int_id", {"id": 9}),
    ("tag/hello-world/", "page_tag_slug_tag", {"tag": "hello-world"}),
    (
        "u/8dbe9c25-6e30-4cc9-9898-52dc697b4bd6/",
        "page_u_uuid_uid",
        {"uid": uuid.UUID("8dbe9c25-6e30-4cc9-9898-52dc697b4bd6")},
    ),
    ("name/mia/", "page_name_username", {"username": "mia"}),
    ("num/abc/", "page_num_str_x", {"x": "abc"}),
    ("files/a/b/c.txt/", "page_files_rest", {"rest": "a/b/c.txt"}),
    ("docs/x/y/end/", "page_docs_chapter_end", {"chapter": "x/y"}),
)

_RESOLVE_CASE_IDS = (
    "root",
    "static",
    "static_nested",
    "int_param",
    "slug_param",
    "uuid_param",
    "default_str_param",
    "int_rejects_nonnumeric",
    "path_tail_with_slashes",
    "path_in_route_middle",
)

# Overlap winners depend on filesystem registration order, so they are
# asserted against the linear scan instead of a pinned route.
_OVERLAP_PATHS = ("articles/latest/", "articles/other/", "num/7/")

_PARITY_FIELDS = (
    "func",
    "args",
    "kwargs",
    "url_name",
    "route",
    "namespaces",
    "app_names",
    "captured_kwargs",
)


def _write_page(tree: Path, route: str) -> None:
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "page.py").write_text('template = "ok"\n')


def _plain_view(_request, **kwargs) -> HttpResponse:
    return HttpResponse(b"plain")


def _other_view(_request, **kwargs) -> HttpResponse:
    return HttpResponse(b"other")


def _late_handler() -> None:
    return None


def _tried_pattern_strings(exc_info) -> set[str]:
    tried = exc_info.value.args[0]["tried"]
    return {str(pattern.pattern) for entry in tried for pattern in entry}


@pytest.fixture()
def page_tree(tmp_path):
    """Real page tree on disk wired as the only PAGE_BACKENDS source."""
    for route in _TREE_ROUTES:
        _write_page(tmp_path, route)
    with override_next_settings(PAGE_BACKENDS=default_page_router_config(tmp_path)):
        yield tmp_path


class TestResolveAgainstPageTree:
    """Trie and linear resolution over a real filesystem page tree."""

    @pytest.mark.parametrize(
        "resolver_path",
        ["next.urls.TrieURLResolver", "django.urls.resolvers.URLResolver"],
        ids=["trie", "linear"],
    )
    @pytest.mark.parametrize(
        ("url", "expected_name", "expected_kwargs"),
        _RESOLVE_CASES,
        ids=_RESOLVE_CASE_IDS,
    )
    def test_resolves_expected_match_in_both_modes(
        self, page_tree, resolver_path, url, expected_name, expected_kwargs
    ) -> None:
        """Both configured resolver classes produce the expected page match."""
        with override_next_settings(URL_RESOLVER=resolver_path):
            match = urlpatterns[0].resolve(url)
        assert match.url_name == expected_name
        assert match.kwargs == expected_kwargs

    @pytest.mark.parametrize(
        "url",
        [case[0] for case in _RESOLVE_CASES] + list(_OVERLAP_PATHS),
        ids=[*_RESOLVE_CASE_IDS, "literal_vs_param", "param_sibling", "int_vs_str"],
    )
    def test_trie_match_is_identical_to_linear_scan(self, page_tree, url) -> None:
        """Every ResolverMatch field matches the inherited linear resolve."""
        resolver = urlpatterns[0]
        fast = resolver.resolve(url)
        linear = URLResolver.resolve(resolver, url)
        for field in _PARITY_FIELDS:
            assert getattr(fast, field) == getattr(linear, field), field

    @pytest.mark.parametrize(
        "url",
        ["missing/", "num/wrong/extra/", "files/", "num//"],
        ids=["unknown", "extra_segment", "empty_path_tail", "empty_param_segment"],
    )
    def test_miss_raises_resolver404_with_vanilla_tried(self, page_tree, url) -> None:
        """A trie miss raises the canonical Resolver404 with the full tried list."""
        resolver = urlpatterns[0]
        with pytest.raises(Resolver404) as fast_exc:
            resolver.resolve(url)
        with pytest.raises(Resolver404) as linear_exc:
            URLResolver.resolve(resolver, url)
        fast_tried = _tried_pattern_strings(fast_exc)
        assert fast_tried
        assert fast_tried == _tried_pattern_strings(linear_exc)

    def test_linear_opt_out_installs_plain_django_resolver(self, page_tree) -> None:
        """The Django resolver dotted path swaps in a resolver with no trie index."""
        with override_next_settings(URL_RESOLVER="django.urls.resolvers.URLResolver"):
            resolver = urlpatterns[0]
            assert type(resolver) is URLResolver
            assert not hasattr(resolver, "_route_index")
            linear_match = resolver.resolve("home/")
        assert linear_match.url_name == "page_home"
        trie_match = urlpatterns[0].resolve("home/")
        assert trie_match.url_name == linear_match.url_name
        assert trie_match.kwargs == linear_match.kwargs

    def test_index_rebuilt_after_router_reload(self, page_tree) -> None:
        """A route added on disk resolves after router_manager.reload()."""
        resolver = urlpatterns[0]
        with pytest.raises(Resolver404):
            resolver.resolve("fresh/")
        _write_page(page_tree, "fresh")
        router_manager.reload()
        assert resolver.resolve("fresh/").url_name == "page_fresh"

    def test_late_action_registration_resolves_without_restart(self, page_tree) -> None:
        """A form action registered after the first resolve gets its URL."""
        resolver = urlpatterns[0]
        resolver.resolve("home/")
        with override_form_action("late_trie_probe", _late_handler):
            match = resolver.resolve("_next/form/some-uid/")
        assert match.url_name == URL_NAME_FORM_ACTION
        assert match.kwargs == {"uid": "some-uid"}

    def test_index_cache_is_reused_while_versions_are_stable(self, page_tree) -> None:
        """Two resolves without a version change share one built index."""
        resolver = urlpatterns[0]
        resolver.resolve("home/")
        cached = resolver._index_cache
        assert cached is not None
        resolver.resolve("items/3/")
        assert resolver._index_cache is cached


class TestReverseOverTrieUrlpatterns:
    """reverse() and page_reverse() on top of the resolver-wrapped urlpatterns."""

    def test_namespaced_reverse(self, page_tree) -> None:
        """The `next` namespace reverses through the TrieURLResolver."""
        url = reverse("next:page_home", urlconf="tests.urls.urls_namespaced")
        assert url == "/home/"

    def test_page_reverse_and_kwargs(self, page_tree) -> None:
        """page_reverse builds page URLs from route templates."""
        with override_settings(ROOT_URLCONF="tests.urls.urls_namespaced"):
            assert page_reverse("home") == "/home/"
            assert page_reverse("items/[int:id]", id=3) == "/items/3/"


class TestTrieOverPlainPatternList:
    """TrieURLResolver over an explicit list without a version token."""

    def test_constant_token_builds_index_once(self) -> None:
        patterns = [
            path("one/", _plain_view, name="one"),
            path("p/<val>/", _plain_view, name="typed"),
        ]
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        assert resolver._current_token() == (0, 0)
        first = resolver.resolve("one/")
        cached = resolver._index_cache
        second = resolver.resolve("p/x/")
        assert resolver._index_cache is cached
        assert first.url_name == "one"
        assert second.kwargs == {"val": "x"}

    def test_stale_appended_pattern_found_via_fallback(self) -> None:
        patterns = [path("one/", _plain_view, name="one")]
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        resolver.resolve("one/")
        cached = resolver._index_cache
        patterns.append(path("late/", _plain_view, name="late"))
        match = resolver.resolve("late/")
        assert match.url_name == "late"
        assert resolver._index_cache is cached

    def test_unindexed_re_path_competes_at_its_original_index(self) -> None:
        patterns = [
            re_path(r"^rx/(?P<num>[0-9]+)/$", _plain_view, name="regex_first"),
            path("rx/<int:num>/", _other_view, name="path_second"),
        ]
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        match = resolver.resolve("rx/5/")
        assert match.url_name == "regex_first"
        assert match.kwargs == {"num": "5"}

    @pytest.mark.parametrize(
        "literal_first", [True, False], ids=["literal_first", "param_first"]
    )
    def test_literal_vs_param_sibling_first_index_wins(self, literal_first) -> None:
        literal = path("box/new/", _plain_view, name="literal")
        param = path("box/<str:item>/", _other_view, name="param")
        patterns = [literal, param] if literal_first else [param, literal]
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        match = resolver.resolve("box/new/")
        assert match.url_name == ("literal" if literal_first else "param")
        assert match.url_name == URLResolver.resolve(resolver, "box/new/").url_name

    @pytest.mark.parametrize("int_first", [True, False], ids=["int_first", "str_first"])
    def test_int_vs_str_param_on_the_same_path(self, int_first) -> None:
        int_pattern = path("v/<int:x>/", _plain_view, name="int")
        str_pattern = path("v/<str:x>/", _other_view, name="str")
        patterns = (
            [int_pattern, str_pattern] if int_first else [str_pattern, int_pattern]
        )
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        assert resolver.resolve("v/7/").url_name == ("int" if int_first else "str")
        assert resolver.resolve("v/word/").url_name == "str"

    def test_nested_resolver_candidate_joins_route_and_namespaces(self) -> None:
        sub = [path("a/<int:n>/", _plain_view, name="sub_a")]
        patterns = [path("inc/", include((sub, "subapp"), namespace="subns"))]
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        fast = resolver.resolve("inc/a/5/")
        linear = URLResolver.resolve(resolver, "inc/a/5/")
        assert fast.url_name == "sub_a"
        assert fast.route == "inc/a/<int:n>/"
        assert fast.app_names == ["subapp"]
        assert fast.namespaces == ["subns"]
        for field in _PARITY_FIELDS:
            assert getattr(fast, field) == getattr(linear, field), field

    def test_nested_resolver_miss_falls_through_to_super(self) -> None:
        sub = [path("a/", _plain_view, name="sub_a")]
        patterns = [path("inc/", include((sub, "subapp")))]
        resolver = TrieURLResolver(RoutePattern(""), patterns)
        with pytest.raises(Resolver404) as exc:
            resolver.resolve("inc/missing/")
        assert any(len(entry) == 2 for entry in exc.value.args[0]["tried"])

    def test_prefix_pattern_mismatch_falls_back_to_super(self) -> None:
        resolver = TrieURLResolver(
            RoutePattern("api/"), [path("x/", _plain_view, name="x")]
        )
        assert resolver.resolve("api/x/").url_name == "x"
        with pytest.raises(Resolver404):
            resolver.resolve("other/x/")
