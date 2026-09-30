from __future__ import annotations

from dataclasses import dataclass

from tests.support.backends import PROJECT_APP_DIRECTORIES_FINDER


@dataclass(frozen=True, slots=True)
class StaticNameCase:
    """One reference the name reader takes, pinned to the verdict it must give.

    `expected` says the reference is an authored name rather than a ready URL, and
    `name` is the normalised lookup key, unset for a URL and for a traversal.
    """

    id: str
    reference: str
    expected: bool
    name: str | None = None
    traverses: bool = False


STATIC_NAME_CASES: tuple[StaticNameCase, ...] = (
    StaticNameCase("bare_name", "css/theme.css", True, name="css/theme.css"),
    StaticNameCase("rooted_path", "/static/x.css", False),
    StaticNameCase("protocol_relative", "//cdn/x.css", False),
    StaticNameCase("absolute_url", "https://cdn/x.css", False),
    StaticNameCase("data_url", "data:text/css,body{}", False),
    StaticNameCase("query_only", "?v=1", False),
    StaticNameCase("fragment_only", "#anchor", False),
    StaticNameCase("windows_drive", "C:\\x.css", False),
    StaticNameCase("empty", "", False),
    StaticNameCase("name_with_query", "app.css?v=2", False),
    StaticNameCase("dot_segment", "./css/x.css", True, name="css/x.css"),
    StaticNameCase("double_slash_inside", "css//x.css", True, name="css/x.css"),
    StaticNameCase("trailing_slash", "css/theme/", True, name="css/theme"),
    StaticNameCase("contained_parent", "a/../b.css", True, name="b.css"),
    StaticNameCase("trailing_newline", "css/x.css\n", True, name="css/x.css"),
    StaticNameCase("embedded_tab", "css/\tx.css", True, name="css/x.css"),
    StaticNameCase("climbs_once", "../b.css", True, traverses=True),
    StaticNameCase("climbs_past_root", "a/../../b.css", True, traverses=True),
    StaticNameCase("bare_parent", "..", True, traverses=True),
    StaticNameCase("climbs_to_etc", "site/../../../etc/x.svg", True, traverses=True),
)


@dataclass(frozen=True, slots=True)
class VersionedUrlCase:
    """One URL and one version value, pinned to the URL the stamp has to yield."""

    id: str
    url: str
    version: object
    expected: str


VERSIONED_URL_CASES: tuple[VersionedUrlCase, ...] = (
    VersionedUrlCase("bare_url", "/static/a.css", "7", "/static/a.css?v=7"),
    VersionedUrlCase(
        "existing_query", "/static/a.css?x=1", "7", "/static/a.css?x=1&v=7"
    ),
    VersionedUrlCase(
        "reserved_character", "/static/a.css", "a&b", "/static/a.css?v=a%26b"
    ),
    VersionedUrlCase("coerced_int", "/static/a.css", 42, "/static/a.css?v=42"),
    VersionedUrlCase(
        "fragment_kept", "/static/a.css#top", "7", "/static/a.css?v=7#top"
    ),
    VersionedUrlCase("none_is_dropped", "/static/a.css", None, "/static/a.css"),
    VersionedUrlCase("empty_is_dropped", "/static/a.css", "", "/static/a.css"),
    VersionedUrlCase("absolute_url", "https://cdn/a.css", "7", "https://cdn/a.css?v=7"),
    VersionedUrlCase("existing_v", "site/app.css?v=2", "b17", "site/app.css?v=b17"),
    VersionedUrlCase(
        "existing_v_among_others",
        "/static/a.css?x=1&v=2&y=3",
        "7",
        "/static/a.css?x=1&y=3&v=7",
    ),
    VersionedUrlCase(
        "neighbours_are_not_re_encoded",
        "/static/a.css?a=b%20c&flag",
        "7",
        "/static/a.css?a=b%20c&flag&v=7",
    ),
    VersionedUrlCase(
        "data_uri",
        "data:text/css;base64,Ym9keXt9",
        "7",
        "data:text/css;base64,Ym9keXt9",
    ),
    VersionedUrlCase(
        "blob_uri",
        "blob:https://example.com/9a38-0f21",
        "7",
        "blob:https://example.com/9a38-0f21",
    ),
    VersionedUrlCase(
        "mailto_uri", "mailto:team@example.com", "7", "mailto:team@example.com"
    ),
)


_STOCK_APP_FINDER = "django.contrib.staticfiles.finders.AppDirectoriesFinder"
_FILESYSTEM_FINDER = "django.contrib.staticfiles.finders.FileSystemFinder"
_NEXT_APP_FINDER = "next.static.NextAppDirectoriesFinder"
_NEXT_FINDER = "next.static.NextStaticFilesFinder"


@dataclass(frozen=True, slots=True)
class AppFinderCase:
    """One ``STATICFILES_FINDERS`` entry, pinned to whether the check refuses it."""

    id: str
    path: object
    refused: bool


APP_FINDER_CASES: tuple[AppFinderCase, ...] = (
    AppFinderCase("project_subclass", PROJECT_APP_DIRECTORIES_FINDER, True),
    AppFinderCase("framework_finder", _NEXT_APP_FINDER, False),
    AppFinderCase("stock_finder", _STOCK_APP_FINDER, False),
    AppFinderCase("filesystem_finder", _FILESYSTEM_FINDER, False),
    AppFinderCase("unimportable", "tests.support.nowhere.Missing", False),
    AppFinderCase("not_a_string", 123, False),
)


@dataclass(frozen=True, slots=True)
class FinderInstallCase:
    """One configured finder list, pinned to the list ``install`` leaves behind."""

    id: str
    configured: tuple[str, ...]
    expected: tuple[str, ...]


FINDER_INSTALL_CASES: tuple[FinderInstallCase, ...] = (
    FinderInstallCase(
        "stock_before_framework",
        (_STOCK_APP_FINDER, _NEXT_APP_FINDER),
        (_NEXT_APP_FINDER, _NEXT_FINDER),
    ),
    FinderInstallCase(
        "framework_before_stock",
        (_NEXT_APP_FINDER, _STOCK_APP_FINDER),
        (_NEXT_APP_FINDER, _NEXT_FINDER),
    ),
    FinderInstallCase(
        "framework_app_finder_twice",
        (_NEXT_APP_FINDER, _NEXT_APP_FINDER),
        (_NEXT_APP_FINDER, _NEXT_FINDER),
    ),
    FinderInstallCase(
        "stock_app_finder_twice",
        (_STOCK_APP_FINDER, _STOCK_APP_FINDER),
        (_NEXT_APP_FINDER, _NEXT_FINDER),
    ),
    FinderInstallCase(
        "framework_finder_twice", (_NEXT_FINDER, _NEXT_FINDER), (_NEXT_FINDER,)
    ),
    FinderInstallCase(
        "other_finder_twice_survives",
        (_FILESYSTEM_FINDER, _FILESYSTEM_FINDER),
        (_FILESYSTEM_FINDER, _FILESYSTEM_FINDER, _NEXT_FINDER),
    ),
    FinderInstallCase(
        "project_subclass_beside_the_framework_one",
        (PROJECT_APP_DIRECTORIES_FINDER, _NEXT_APP_FINDER),
        (PROJECT_APP_DIRECTORIES_FINDER, _NEXT_APP_FINDER, _NEXT_FINDER),
    ),
    FinderInstallCase(
        "order_is_preserved",
        (_FILESYSTEM_FINDER, _STOCK_APP_FINDER, _NEXT_APP_FINDER, _NEXT_FINDER),
        (_FILESYSTEM_FINDER, _NEXT_APP_FINDER, _NEXT_FINDER),
    ),
)


@dataclass(frozen=True, slots=True)
class EmptyAssetCase:
    """One ``{% asset %}`` reference the tag refuses before the manager is asked."""

    id: str
    reference: object


EMPTY_ASSET_CASES: tuple[EmptyAssetCase, ...] = (
    EmptyAssetCase("empty_string", ""),
    EmptyAssetCase("none", None),
    EmptyAssetCase("integer", 123),
    EmptyAssetCase("bytes", b"css/app.css"),
)
