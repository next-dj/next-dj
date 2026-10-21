import sys
from collections.abc import Sequence
from types import ModuleType
from unittest.mock import patch

import pytest
from django.core.exceptions import AppRegistryNotReady
from django.urls import clear_url_caches, get_resolver, include, path
from django.urls.resolvers import RegexPattern, URLResolver
from django.utils import translation

from next.urls import urlconf
from next.urls.urlconf import (
    NO_MOUNTS,
    Mount,
    MountPoint,
    UrlconfMounts,
    outer_kwargs,
    urlconf_mounts,
)
from next.utils import PageRoot
from tests.support import I18N, installed_apps, write_app, write_urlconf


class _Untouchable(Sequence):
    """A lazy pattern list the walk must leave alone."""

    def __getitem__(self, index: int) -> object:
        raise AssertionError

    def __len__(self) -> int:
        raise AssertionError


def _mounted(urlconf_name: str, root, *names: str):
    with installed_apps(root, *names, urlconf=urlconf_name):
        return urlconf_mounts()


class TestUrlconfWalk:
    """The walk records where `next.urls` and every `pages()` tree are included."""

    def test_the_framework_urlconf_is_the_include_point(self) -> None:
        found = urlconf_mounts()

        assert (found.include_point, found.points, found.claimed) == (
            "",
            (),
            frozenset(),
        )

    @pytest.mark.parametrize(
        ("entries", "include_point", "prefixes", "index"),
        [
            (('path("app/", include("next.urls"))',), "app/", [], 0),
            (('path("audit/", pages("audit"))',), None, ["audit/"], 0),
            (
                (
                    'path("a/", pages("audit"))',
                    'path("", include("next.urls"))',
                    'path("b/", pages("audit", namespace="b"))',
                ),
                "",
                ["a/", "b/"],
                1,
            ),
            (
                (
                    'path("org/<int:org>/", include([path("audit/", pages("audit"))]))',
                    'path("x/", include([path("y/", include("next.urls"))]))',
                ),
                "x/y/",
                ["org/<int:org>/audit/"],
                1,
            ),
            (
                (
                    'path("one/", include("next.urls"))',
                    'path("two/", include("next.urls", namespace="again"))',
                ),
                "one/",
                [],
                0,
            ),
        ],
        ids=["prefixed_include", "not_included", "around", "nested", "first_wins"],
    )
    def test_where_each_include_sits(
        self, tmp_path, entries, include_point, prefixes, index
    ) -> None:
        write_app(tmp_path, "audit")
        urlconf_name = write_urlconf(tmp_path, "walk_urls", *entries)

        found = _mounted(urlconf_name, tmp_path, "audit")

        assert found.include_point == include_point
        assert [point.prefix for point in found.points] == prefixes
        assert found.include_index == index
        assert found.claimed == (
            frozenset({("audit", "pages")}) if prefixes else frozenset()
        )

    def test_a_regex_include_joins_without_a_second_anchor(self, tmp_path) -> None:
        write_app(tmp_path, "audit")
        (tmp_path / "regex_urls.py").write_text(
            "from django.urls import include, re_path\n\n"
            "from next.urls import pages\n\n\n"
            "urlpatterns = [\n"
            '    re_path(r"^a/", include([re_path(r"^b/", pages("audit"))])),\n'
            "]\n"
        )

        found = _mounted("regex_urls", tmp_path, "audit")

        assert [point.prefix for point in found.points] == ["^a/b/"]
        assert [point.trail for point in found.points] == ["a/b"]

    def test_a_regex_include_reads_as_the_trail_django_reverses(self, tmp_path) -> None:
        write_app(tmp_path, "audit")
        (tmp_path / "regex_trail_urls.py").write_text(
            "from django.urls import include, path, re_path\n\n"
            "from next.urls import pages\n\n\n"
            "urlpatterns = [\n"
            '    path("o/<int:org>/", include([re_path(r"^y/(?P<year>[0-9]{4})/", '
            'pages("audit"))])),\n'
            "]\n"
        )

        found = _mounted("regex_trail_urls", tmp_path, "audit")

        assert [point.trail for point in found.points] == ["o/[int:org]/y/[year]"]

    def test_a_tuple_of_patterns_is_walked(self, tmp_path) -> None:
        write_app(tmp_path, "audit")
        (tmp_path / "tuple_urls.py").write_text(
            "from django.urls import path\n\n"
            "from next.urls import pages\n\n\n"
            'urlpatterns = (path("t/", pages("audit")),)\n'
        )

        found = _mounted("tuple_urls", tmp_path, "audit")

        assert [point.prefix for point in found.points] == ["t/"]

    def test_no_lazy_sequence_is_iterated(self, tmp_path) -> None:
        write_app(tmp_path, "audit")
        urlconf_name = write_urlconf(
            tmp_path,
            "lazy_urls",
            'path("a/", pages("audit"))',
            'path("", include("next.urls"))',
        )

        with (
            installed_apps(tmp_path, "audit", urlconf=urlconf_name),
            patch("next.urls.mount._tree_patterns") as built,
            patch("next.urls.manager._LazyUrlPatterns._patterns") as assembled,
        ):
            clear_url_caches()
            resolver = get_resolver()
            resolver.url_patterns.append(
                URLResolver(RegexPattern(r"^lazy/"), _Untouchable())
            )
            found = urlconf_mounts()

        assert found.include_point == ""
        assert len(found.points) == 1
        built.assert_not_called()
        assembled.assert_not_called()


class TestIncludeKwargs:
    """The walk records every kwarg the includes around a tree hand its pages."""

    def test_captured_groups_and_extra_kwargs_add_up(self, tmp_path) -> None:
        write_app(tmp_path, "audit")
        (tmp_path / "kwargs_walk_urls.py").write_text(
            "from django.urls import include, path, re_path\n\n"
            "from next.urls import pages\n\n\n"
            "urlpatterns = [\n"
            '    re_path(r"^o/(?P<org>[0-9]+)/(x)/", include(\n'
            '        [path("a/<int:team>/", pages("audit"))]\n'
            "    )),\n"
            '    path("t/<slug:tenant>/", include("next.urls"), {"mode": "x"}),\n'
            "]\n"
        )

        found = _mounted("kwargs_walk_urls", tmp_path, "audit")

        assert [point.kwargs for point in found.points] == [{"org", "team"}]
        assert found.include_kwargs == {"tenant", "mode"}

    def test_a_tree_reads_the_kwargs_of_the_include_that_mounts_it(
        self, tmp_path
    ) -> None:
        mount = Mount("audit", "audit", "pages", tmp_path, "audit")
        mounts = UrlconfMounts(
            include_kwargs=frozenset({"tenant"}),
            points=(MountPoint(mount, "o/<int:org>/", frozenset({"org"})),),
        )
        mounted = PageRoot(tmp_path, "x", prefix="o/<int:org>/")
        stray = PageRoot(tmp_path, "x", prefix="gone/")
        assembled = PageRoot(tmp_path, "x")

        assert outer_kwargs(mounted, mounts) == {"org"}
        assert outer_kwargs(stray, mounts) == frozenset()
        assert outer_kwargs(assembled, mounts) == {"tenant"}


class TestUrlconfWalkMemo:
    """The walk runs once per root resolver and again once the URL caches clear."""

    def test_one_walk_per_resolver(self) -> None:
        clear_url_caches()

        with patch.object(
            urlconf._Walk, "visit", autospec=True, side_effect=urlconf._Walk.visit
        ) as visit:
            first = urlconf_mounts()
            again = urlconf_mounts()
            walks = visit.call_count
            clear_url_caches()
            urlconf_mounts()

        assert first is again
        assert walks == 1
        assert visit.call_count == 2

    def test_a_half_imported_urlconf_is_walked_again(
        self, tmp_path, monkeypatch
    ) -> None:
        write_app(tmp_path, "audit")
        half = ModuleType("half_urls")
        monkeypatch.setitem(sys.modules, "half_urls", half)

        with installed_apps(tmp_path, "audit", urlconf="half_urls"):
            early = urlconf_mounts()
            half.urlpatterns = [path("", include("next.urls"))]
            later = urlconf_mounts()
            again = urlconf_mounts()

        assert early.include_point is None
        assert later.include_point == ""
        assert again is later

    def test_a_urlconf_that_fails_to_import_mounts_nothing(self, settings) -> None:
        settings.ROOT_URLCONF = "tests.urls.no_such_urlconf"
        found = urlconf_mounts()

        assert found is NO_MOUNTS

    def test_no_root_urlconf_mounts_nothing(self, settings) -> None:
        del settings.ROOT_URLCONF

        assert urlconf_mounts() is NO_MOUNTS


class TestLocalePrefix:
    """An `i18n_patterns()` prefix is read in `LANGUAGE_CODE`, not the active one."""

    def test_the_prefix_reads_in_the_default_language(self, tmp_path) -> None:
        write_app(tmp_path, "audit", {"pages": ("log",)})
        (tmp_path / "i18n_urls.py").write_text(
            "from django.conf.urls.i18n import i18n_patterns\n"
            "from django.urls import path\n\n"
            "from next.urls import pages\n\n\n"
            'urlpatterns = i18n_patterns(path("audit/", pages("audit")))\n'
        )

        with (
            installed_apps(tmp_path, "audit", urlconf="i18n_urls", **I18N),
            translation.override("de"),
        ):
            found = urlconf_mounts()

        assert [point.prefix for point in found.points] == ["en/audit/"]


class TestRegistryNotReady:
    """A walk before the app registry is ready answers no mounts and is not kept."""

    def test_a_registry_that_is_not_ready_mounts_nothing_and_is_walked_again(
        self, tmp_path
    ) -> None:
        write_app(tmp_path, "audit", {"pages": ("log",)})
        urlconf = write_urlconf(tmp_path, "early_urls", 'path("a/", pages("audit"))')

        with installed_apps(tmp_path, "audit", urlconf=urlconf):
            with patch(
                "next.urls.mount.apps.get_app_configs",
                side_effect=AppRegistryNotReady("Apps aren't loaded yet."),
            ):
                early = urlconf_mounts()
            later = urlconf_mounts()

        assert early is NO_MOUNTS
        assert [point.prefix for point in later.points] == ["a/"]
