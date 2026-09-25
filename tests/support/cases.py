from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from next.pages.errors import PageMetadataShapeError, PageMetadataURLError
from next.pages.metadata import OpenGraph, Robots
from next.urls import DUrl
from tests.support.backends import PROJECT_APP_DIRECTORIES_FINDER
from tests.support.helpers import file_router_config_entry


if TYPE_CHECKING:
    from collections.abc import Callable

    from django.http import HttpRequest

    from next.deps import DependencyResolver


_UUID_TEXT = "12345678-1234-5678-1234-567812345678"
_UUID_VALUE = UUID(_UUID_TEXT)


@dataclass(frozen=True, slots=True)
class CoerceUrlValueCase:
    """One row for ``TestCoerceUrlValue`` (raw value, type hint, expected value)."""

    id: str
    raw: object
    hint: object
    expected: object


COERCE_URL_VALUE_CASES: tuple[CoerceUrlValueCase, ...] = (
    CoerceUrlValueCase("int_ok", "42", int, 42),
    CoerceUrlValueCase("int_bad", "x", int, "x"),
    CoerceUrlValueCase("bool_true", "true", bool, True),
    CoerceUrlValueCase("bool_one", "1", bool, True),
    CoerceUrlValueCase("bool_yes", "yes", bool, True),
    CoerceUrlValueCase("bool_zero", "0", bool, False),
    CoerceUrlValueCase("bool_false", "false", bool, False),
    CoerceUrlValueCase("float_ok", "3.14", float, 3.14),
    CoerceUrlValueCase("float_bad", "x", float, "x"),
    CoerceUrlValueCase("str_pass", "hello", str, "hello"),
    CoerceUrlValueCase("uuid_ok", _UUID_TEXT, UUID, _UUID_VALUE),
    CoerceUrlValueCase("uuid_bad", "not-a-uuid", UUID, "not-a-uuid"),
    CoerceUrlValueCase("decimal_ok", "3.14", Decimal, Decimal("3.14")),
    CoerceUrlValueCase("decimal_bad", "x", Decimal, "x"),
    CoerceUrlValueCase("date_ok", "2026-01-15", date, date(2026, 1, 15)),
    CoerceUrlValueCase("date_bad", "x", date, "x"),
    CoerceUrlValueCase(
        "datetime_ok",
        "2026-01-15T10:30:00+00:00",
        datetime,
        datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
    ),
    CoerceUrlValueCase("datetime_bad", "x", datetime, "x"),
    CoerceUrlValueCase("isinstance_uuid", _UUID_VALUE, UUID, _UUID_VALUE),
    CoerceUrlValueCase("isinstance_int", 42, int, 42),
    CoerceUrlValueCase("non_type_hint", "anything", "not-a-type", "anything"),
    CoerceUrlValueCase("str_from_int", 42, str, "42"),
    CoerceUrlValueCase("unsupported_type", "hello", bytes, "hello"),
)


@dataclass(frozen=True, slots=True)
class UrlKwargsResolveCase:
    """One row for ``UrlKwargsProvider.resolve`` table tests."""

    id: str
    name: str
    annotation: object
    url_kwargs: dict[str, object]
    expected: object


URL_KWARGS_RESOLVE_CASES: tuple[UrlKwargsResolveCase, ...] = (
    UrlKwargsResolveCase("int_match", "id", int, {"id": 42}, 42),
    UrlKwargsResolveCase("str_to_int", "id", int, {"id": "99"}, 99),
    UrlKwargsResolveCase(
        "no_annotation", "slug", inspect.Parameter.empty, {"slug": "hello"}, "hello"
    ),
    UrlKwargsResolveCase(
        "int_conv_fail", "id", int, {"id": "not-a-number"}, "not-a-number"
    ),
    UrlKwargsResolveCase(
        "str_annot", "slug", str, {"slug": "hello-world"}, "hello-world"
    ),
    UrlKwargsResolveCase("missing_key", "missing", str, {"other": "value"}, None),
    UrlKwargsResolveCase(
        "uuid_preserved", "id", UUID, {"id": _UUID_VALUE}, _UUID_VALUE
    ),
    UrlKwargsResolveCase("uuid_from_text", "id", UUID, {"id": _UUID_TEXT}, _UUID_VALUE),
)


@dataclass(frozen=True, slots=True)
class UrlByAnnotationResolveCase:
    """One row for ``UrlByAnnotationProvider.resolve`` table tests."""

    id: str
    name: str
    annotation: object
    url_kwargs: dict[str, object]
    expected: object | None


URL_BY_ANNOTATION_RESOLVE_CASES: tuple[UrlByAnnotationResolveCase, ...] = (
    UrlByAnnotationResolveCase("coerce_int", "pk", DUrl[int], {"pk": "123"}, 123),
    UrlByAnnotationResolveCase(
        "str_slug", "slug", DUrl[str], {"slug": "hello"}, "hello"
    ),
    UrlByAnnotationResolveCase("missing_key", "missing", DUrl[str], {}, None),
    UrlByAnnotationResolveCase(
        "two_arg_coerce_int", "note_id", DUrl["id", int], {"id": "42"}, 42
    ),
    UrlByAnnotationResolveCase(
        "key_only_no_coercion", "note_id", DUrl["id"], {"id": "7"}, "7"
    ),
    UrlByAnnotationResolveCase(
        "two_arg_reads_named_key", "note_id", DUrl["id", int], {"note_id": "9"}, None
    ),
    UrlByAnnotationResolveCase(
        "coerce_uuid_preserved", "pk", DUrl[UUID], {"pk": _UUID_VALUE}, _UUID_VALUE
    ),
    UrlByAnnotationResolveCase(
        "coerce_uuid_from_text", "pk", DUrl[UUID], {"pk": _UUID_TEXT}, _UUID_VALUE
    ),
    UrlByAnnotationResolveCase(
        "annotated_coerce_int", "pk", Annotated[DUrl[int], "tenant"], {"pk": "123"}, 123
    ),
    UrlByAnnotationResolveCase(
        "annotated_named_key",
        "note_id",
        Annotated[DUrl["id", int], "tenant"],
        {"id": "42"},
        42,
    ),
)


@dataclass(frozen=True, slots=True)
class PlanCase:
    """One callable resolved in one context, pinned to the literal it must yield.

    `kwargs` is the loose mapping `resolve_dependencies` takes, and `expected`
    the mapping a compile and a replay of the plan both have to produce.
    """

    id: str
    func: Callable[..., object]
    kwargs: dict[str, object]
    expected: dict[str, object]


@dataclass(frozen=True, slots=True)
class TemplateContextCase:
    """One callable resolved the way a component render resolves it.

    `template_context` is the mapping the tag hands the resolver, and `expected` the
    literal both the compile and the replay have to produce.
    """

    id: str
    func: Callable[..., object]
    request: HttpRequest | None
    template_context: dict[str, object] | None
    expected: dict[str, object]


@dataclass(frozen=True, slots=True)
class ParityCase:
    """One callable and one loose kwargs mapping both resolvers have to agree on.

    `build` installs whatever providers or dependencies the case needs, so the two
    resolvers under comparison are set up identically and independently.
    """

    id: str
    func: Callable[..., object]
    kwargs: dict[str, object] = field(default_factory=dict)
    build: Callable[[DependencyResolver], None] = lambda _r: None


@dataclass(frozen=True, slots=True)
class ContextMarkerCase:
    """One `Context` marker source, resolved against one template context.

    `source` is what the marker was built with, a name, a callable, a constant, or None
    for the parameter name, and `expected` is what both paths have to answer.
    """

    id: str
    source: object
    context_data: dict[str, object]
    expected: object


# Sentinels the matrix reads specially: RAISE makes the hook raise
# PermissionDenied, BAD_TYPE makes it return an unsupported type.
PERMISSION_HOOK_RAISE = object()
PERMISSION_HOOK_BAD_TYPE = object()


@dataclass(frozen=True, slots=True)
class PermissionHookCase:
    """One row for the dynamic permission-hook return-contract matrix."""

    id: str
    hook_return: object
    expected_status: int | None
    expected_redirect: str | None = None
    raises_permission_denied: bool = False
    raises_type_error: bool = False


PERMISSION_OUTCOME_CASES: tuple[PermissionHookCase, ...] = (
    PermissionHookCase("none_allows", None, 302, expected_redirect="/"),
    PermissionHookCase("true_allows", True, 302, expected_redirect="/"),
    PermissionHookCase("false_denies", False, None, raises_permission_denied=True),
    PermissionHookCase(
        "redirect_short_circuits", "redirect", 302, expected_redirect="/paywall/"
    ),
    PermissionHookCase("response_403_verbatim", "response_403", 403),
    PermissionHookCase(
        "raised_propagates", PERMISSION_HOOK_RAISE, None, raises_permission_denied=True
    ),
    PermissionHookCase(
        "bad_type_raises_type_error",
        PERMISSION_HOOK_BAD_TYPE,
        None,
        raises_type_error=True,
    ),
)


@dataclass(frozen=True, slots=True)
class WatchSourcesCase:
    """What the reloader reports for one finder run, spelled relative to the tree.

    `rooted` decides whether the tree is reported as a page root at all, which is what
    tells a path under no page tree from one the finder can name.
    """

    rooted: bool = True
    templates: tuple[str, ...] = ()
    layouts: tuple[str, ...] = ()
    components: tuple[str, ...] = ()


# The component folder is the one watch source a page tree does not carry on its own,
# so this row is what makes the finder walk its component branch at all.
TEMPLATE_AND_COMPONENT_SOURCES: WatchSourcesCase = WatchSourcesCase(
    templates=("about/template.djx",), components=("_components/widget/component.py",)
)


@dataclass(frozen=True, slots=True)
class WatchedBackendsCase:
    """One ``PAGE_BACKENDS`` shape the watcher reads its page trees from.

    ``entries`` names the entry shapes a test builds under its own ``tmp_path``,
    because a configured tree only exists once a test has a directory to make.
    """

    id: str
    entries: tuple[str, ...]


WATCHED_BACKENDS_CASES: tuple[WatchedBackendsCase, ...] = (
    WatchedBackendsCase("no_entry", ()),
    WatchedBackendsCase("not_a_dict", ("not_a_dict",)),
    WatchedBackendsCase("unimportable", ("unimportable",)),
    WatchedBackendsCase("existing_root", ("existing",)),
    WatchedBackendsCase("missing_root", ("missing",)),
    WatchedBackendsCase("app_trees", ("app_dirs",)),
    WatchedBackendsCase("skip_name_entry", ("skipping",)),
    WatchedBackendsCase("existing_and_missing", ("existing", "missing")),
    WatchedBackendsCase("unimportable_and_extra_root", ("unimportable", "extra_root")),
)


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


@dataclass(frozen=True, slots=True)
class PageContentCase:
    """One routed directory, pinned to the counts ``check_page_functions`` reports.

    An unset `template_djx` or `layout_djx` means the file is never written, which
    is what tells a page with no body source from one carrying a sibling template.
    """

    id: str
    page_content: str = ""
    template_djx: str | None = None
    layout_djx: str | None = None
    errors: int = 0
    warnings: int = 0


PAGE_CONTENT_CASES: tuple[PageContentCase, ...] = (
    PageContentCase("with_template", 'template = "Hello World"'),
    PageContentCase(
        "with_render", 'def render(request, **kwargs):\n    return "Hello World"'
    ),
    PageContentCase("with_template_djx", template_djx="<h1>Hello World</h1>"),
    PageContentCase("with_layout_djx", layout_djx="<html>{% template %}</html>"),
    PageContentCase("no_content", errors=1),
)


@dataclass(frozen=True, slots=True)
class PageBodySourceCase:
    """One combination of body sources, pinned to the ``next.W043`` report it earns.

    `winner` and `shadowed` stay unset for a page carrying one source, which is silent.
    """

    id: str
    page_content: str = ""
    template_djx: bool = False
    warnings: int = 0
    winner: str | None = None
    shadowed: str | None = None


_RENDER_BODY = 'def render(request, **kwargs):\n    return "x"'
_TEMPLATE_AND_RENDER = f'template = "x"\n{_RENDER_BODY}'


PAGE_BODY_SOURCE_CASES: tuple[PageBodySourceCase, ...] = (
    PageBodySourceCase(
        "render_and_template_djx",
        _RENDER_BODY,
        template_djx=True,
        warnings=1,
        winner="render()",
        shadowed="template.djx",
    ),
    PageBodySourceCase(
        "render_and_template_attr",
        _TEMPLATE_AND_RENDER,
        warnings=1,
        winner="render()",
        shadowed="template",
    ),
    PageBodySourceCase(
        "template_attr_and_template_djx",
        'template = "x"',
        template_djx=True,
        warnings=1,
        winner="template",
        shadowed="template.djx",
    ),
    PageBodySourceCase(
        "all_three",
        _TEMPLATE_AND_RENDER,
        template_djx=True,
        warnings=1,
        winner="render()",
        shadowed="template, template.djx",
    ),
    PageBodySourceCase("only_render", _RENDER_BODY),
    PageBodySourceCase("only_template_attr", 'template = "x"'),
    PageBodySourceCase("only_template_djx", template_djx=True),
)


@dataclass(frozen=True, slots=True)
class UrlPatternCase:
    """One page on disk, pinned to the pattern and body `create_url_pattern` yields.

    A `pattern_name` of `None` is a directory with no body source, which routes nothing.
    """

    id: str
    page_content: str | None = None
    template_djx: str | None = None
    route: str = "test"
    pattern_name: str | None = "page_test"
    composed: str | None = None


_RENDER_RESPONSE = (
    "from django.http import HttpResponse\n\n\n"
    "def render(request, **kwargs):\n"
    '    return HttpResponse("Hello from render function!")\n'
)
_VIRTUAL_DJX = "<h1>Virtual view: {{ title }}</h1><p>{{ content }}</p>"
_PARAMETER_DJX = "<h1>User: {{ user_id }}</h1><p>Post: {{ post_id }}</p>"


URL_PATTERN_CASES: tuple[UrlPatternCase, ...] = (
    UrlPatternCase("render_function_only", page_content=_RENDER_RESPONSE),
    UrlPatternCase(
        "template_priority",
        page_content='template = "Python template: {{ name }}"',
        template_djx="<h1>DJX template: {{ name }}</h1>",
        composed="Python template: {{ name }}",
    ),
    UrlPatternCase(
        "virtual_view_djx", template_djx=_VIRTUAL_DJX, composed=_VIRTUAL_DJX
    ),
    UrlPatternCase("virtual_view_no_djx", pattern_name=None),
    UrlPatternCase(
        "virtual_view_with_params",
        template_djx=_PARAMETER_DJX,
        route="user/[int:user_id]/post/[int:post_id]",
        pattern_name="page_user_int_user_id_post_int_post_id",
        composed=_PARAMETER_DJX,
    ),
)


@dataclass(frozen=True, slots=True)
class TemplatePriorityCase:
    """One page beside a `template.djx`, pinned to the body a render interpolates."""

    id: str
    page_content: str
    template_djx: str
    template: str


TEMPLATE_PRIORITY_CASES: tuple[TemplatePriorityCase, ...] = (
    TemplatePriorityCase(
        "djx_template_only",
        'print("test")',
        "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>",
        "<h1>{{ title }}</h1><p>Hello {{ name }}!</p>",
    ),
    TemplatePriorityCase(
        "template_attribute_wins",
        'template = "Python template: {{ name }}"',
        "<h1>DJX template: {{ name }}</h1>",
        "Python template: {{ name }}",
    ),
)


@dataclass(frozen=True, slots=True)
class LayoutConfigCase:
    """One ``PAGE_BACKENDS`` list the layout loader finds no extra layout file in."""

    id: str
    config: tuple[object, ...]


LAYOUT_CONFIG_CASES: tuple[LayoutConfigCase, ...] = (
    LayoutConfigCase(
        "stray_entry_beside_a_missing_dir",
        ("invalid_config", file_router_config_entry(pages_dir="/nonexistent/path")),
    ),
    LayoutConfigCase("app_dirs_only", (file_router_config_entry(app_dirs=True),)),
)


@dataclass(frozen=True, slots=True)
class PagesDirsConfigCase:
    """One router entry the layout loader reads its page roots from.

    `roots_the_tree` is the one row whose ``DIRS`` names a directory that exists, so the
    expected roots are the test's own tree rather than a path pinned here.
    """

    id: str
    app_dirs: bool = False
    roots_the_tree: bool = False


PAGES_DIRS_CONFIG_CASES: tuple[PagesDirsConfigCase, ...] = (
    PagesDirsConfigCase("dirs_naming_an_existing_tree", roots_the_tree=True),
    PagesDirsConfigCase("app_dirs_only", app_dirs=True),
    PagesDirsConfigCase("neither_dirs_nor_app_dirs"),
)


@dataclass(frozen=True, slots=True)
class PageRenderCase:
    """One template beside its registered context, pinned to the HTML a render yields.

    `context` maps a registration key to its callable, with `None` for the keyless one,
    and `kwargs` is what the caller passes `render` on top of that.
    """

    id: str
    template: str
    expected: str
    context: dict[str | None, Callable[..., object]] = field(default_factory=dict)
    kwargs: dict[str, object] = field(default_factory=dict)


PAGE_RENDER_CASES: tuple[PageRenderCase, ...] = (
    PageRenderCase(
        "template_only", "Hello {{ name }}!", "Hello World!", kwargs={"name": "World"}
    ),
    PageRenderCase(
        "context_with_keys",
        "Hello {{ user_name }}! You have {{ item_count }} items.",
        "Hello Alice! You have 5 items.",
        context={"user_name": lambda: "Alice", "item_count": lambda: 5},
    ),
    PageRenderCase(
        "context_without_keys",
        "Hello {{ name }}! Status: {{ status }}",
        "Hello Bob! Status: active",
        context={None: lambda: {"name": "Bob", "status": "active"}},
    ),
    PageRenderCase(
        "mixed_context",
        "Hello {{ name }}! Role: {{ role }}. Items: {{ count }}",
        "Hello Charlie! Role: admin. Items: 10",
        context={
            None: lambda: {"name": "Charlie", "role": "admin"},
            "count": lambda: 10,
        },
    ),
    PageRenderCase(
        "context_wins_over_the_caller",
        "Hello {{ name }}! Count: {{ count }}",
        "Hello ContextName! Count: 5",
        context={None: lambda *args, **kwargs: {"name": "ContextName", "count": 5}},
        kwargs={"name": "OverrideName", "count": 20},
    ),
    PageRenderCase(
        "no_context", "Hello {{ name }}!", "Hello Test!", kwargs={"name": "Test"}
    ),
    PageRenderCase("empty_context", "Static content", "Static content"),
)


@dataclass(frozen=True, slots=True)
class MetadataShapeCase:
    """One raw metadata value `normalize_metadata` rejects, and why."""

    id: str
    raw: object
    site: bool
    detail_fragment: str


METADATA_SHAPE_CASES: tuple[MetadataShapeCase, ...] = (
    MetadataShapeCase(
        "non_mapping", ["x"], False, "declares metadata as 'list', expected a mapping"
    ),
    MetadataShapeCase(
        "unknown_top_key",
        {"foo": 1},
        False,
        "declares metadata key 'foo', expected one of alternates, base, canonical, "
        "description, jsonld, og, other, robots, site_name, title, twitter, "
        "verification",
    ),
    MetadataShapeCase(
        "non_str_top_key", {1: "x"}, False, "declares metadata key '1', expected one of"
    ),
    MetadataShapeCase(
        "description_not_text",
        {"description": 5},
        False,
        "declares metadata key 'description' as 'int', expected text",
    ),
    MetadataShapeCase(
        "base_not_str",
        {"base": 1},
        False,
        "declares metadata key 'base' as 'int', expected a string",
    ),
    MetadataShapeCase(
        "canonical_not_str_or_bool",
        {"canonical": 1},
        False,
        "declares metadata key 'canonical' as 'int', expected a string or a bool",
    ),
    MetadataShapeCase(
        "title_wrong_type",
        {"title": 5},
        False,
        "declares metadata key 'title' as 'int', expected text or a mapping",
    ),
    MetadataShapeCase(
        "title_unknown_key",
        {"title": {"foo": "x"}},
        False,
        "declares metadata key 'title.foo', expected one of absolute, default, "
        "template",
    ),
    MetadataShapeCase(
        "title_template_not_text",
        {"title": {"template": 5}},
        False,
        "declares metadata key 'title.template' as 'int', expected text",
    ),
    MetadataShapeCase(
        "site_bare_title",
        {"title": "Acme"},
        True,
        "declares metadata key 'title' as 'str', expected a mapping",
    ),
    MetadataShapeCase(
        "site_absolute_title",
        {"title": {"absolute": "Acme"}},
        True,
        "declares metadata key 'title.absolute', expected one of default, template",
    ),
    MetadataShapeCase(
        "robots_wrong_type",
        {"robots": 1},
        False,
        "declares metadata key 'robots' as 'int', expected a string or a mapping",
    ),
    MetadataShapeCase(
        "robots_unknown_key",
        {"robots": {"foo": True}},
        False,
        "declares metadata key 'robots.foo', expected one of follow, googlebot, "
        "index, max_image_preview, max_snippet, max_video_preview, noarchive, "
        "noimageindex, nosnippet, notranslate, unavailable_after",
    ),
    MetadataShapeCase(
        "robots_flag_not_bool",
        {"robots": {"index": "yes"}},
        False,
        "declares metadata key 'robots.index' as 'str', expected a bool",
    ),
    MetadataShapeCase(
        "robots_limit_given_bool",
        {"robots": {"max_snippet": True}},
        False,
        "declares metadata key 'robots.max_snippet' as 'bool', expected an int",
    ),
    MetadataShapeCase(
        "googlebot_nests_googlebot",
        {"robots": {"googlebot": {"googlebot": "none"}}},
        False,
        "declares metadata key 'robots.googlebot.googlebot', expected one of",
    ),
    MetadataShapeCase(
        "og_unknown_key",
        {"og": {"foo": 1}},
        False,
        "declares metadata key 'og.foo', expected one of article, description, "
        "images, locale, site_name, title, type, url",
    ),
    MetadataShapeCase(
        "og_images_not_sequence",
        {"og": {"images": "/a.png"}},
        False,
        "declares metadata key 'og.images' as 'str', expected a sequence where "
        "each item is a string or a mapping",
    ),
    MetadataShapeCase(
        "og_image_wrong_item",
        {"og": {"images": [1]}},
        False,
        "declares metadata key 'og.images[0]' as 'int', expected a string or a mapping",
    ),
    MetadataShapeCase(
        "og_image_unknown_key",
        {"og": {"images": [{"src": "/a.png"}]}},
        False,
        "declares metadata key 'og.images[0].src', expected one of alt, height, "
        "url, width",
    ),
    MetadataShapeCase(
        "og_image_width_not_int",
        {"og": {"images": ["/a.png", {"width": "1"}]}},
        False,
        "declares metadata key 'og.images[1].width' as 'str', expected an int",
    ),
    MetadataShapeCase(
        "og_article_unknown_key",
        {"og": {"article": {"foo": 1}}},
        False,
        "declares metadata key 'og.article.foo', expected one of authors, "
        "modified_time, published_time, section, tags",
    ),
    MetadataShapeCase(
        "og_article_author_not_str",
        {"og": {"article": {"authors": ["ada", 1]}}},
        False,
        "declares metadata key 'og.article.authors[1]' as 'int', expected a string",
    ),
    MetadataShapeCase(
        "og_article_time_wrong_type",
        {"og": {"article": {"published_time": 1}}},
        False,
        "declares metadata key 'og.article.published_time' as 'int', expected a "
        "datetime or a string",
    ),
    MetadataShapeCase(
        "twitter_unknown_key",
        {"twitter": {"foo": 1}},
        False,
        "declares metadata key 'twitter.foo', expected one of card, creator, "
        "description, images, site, title",
    ),
    MetadataShapeCase(
        "twitter_images_not_sequence",
        {"twitter": {"images": 1}},
        False,
        "declares metadata key 'twitter.images' as 'int', expected a sequence "
        "where each item is a string",
    ),
    MetadataShapeCase(
        "alternates_unknown_key",
        {"alternates": {"foo": 1}},
        False,
        "declares metadata key 'alternates.foo', expected one of languages, x_default",
    ),
    MetadataShapeCase(
        "alternates_languages_wrong_values",
        {"alternates": {"languages": {"en": 1}}},
        False,
        "declares metadata key 'alternates.languages' as 'dict', expected a bool "
        "or a mapping of language codes to URLs",
    ),
    MetadataShapeCase(
        "verification_unknown_engine",
        {"verification": {"duck": "x"}},
        False,
        "declares metadata key 'verification.duck', expected one of bing, google, "
        "yandex",
    ),
    MetadataShapeCase(
        "verification_wrong_scalar",
        {"verification": {"google": 1}},
        False,
        "declares metadata key 'verification.google' as 'int', expected a string "
        "or a sequence where each item is a string",
    ),
    MetadataShapeCase(
        "verification_wrong_item",
        {"verification": {"google": ["a", 1]}},
        False,
        "declares metadata key 'verification.google[1]' as 'int', expected a string",
    ),
    MetadataShapeCase(
        "other_not_mapping",
        {"other": [("a", "b")]},
        False,
        "declares metadata key 'other' as 'list', expected a mapping of names to "
        "text or sequences of text",
    ),
    MetadataShapeCase(
        "other_value_not_text",
        {"other": {"a": 1}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping of names to "
        "text or sequences of text",
    ),
    MetadataShapeCase(
        "other_value_bytes",
        {"other": {"a": b"x"}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "other_key_not_str",
        {"other": {1: "x"}},
        False,
        "declares metadata key 'other' as 'dict', expected a mapping",
    ),
    MetadataShapeCase(
        "jsonld_wrong_type",
        {"jsonld": "x"},
        False,
        "declares metadata key 'jsonld' as 'str', expected a mapping or a sequence "
        "where each item is a mapping",
    ),
    MetadataShapeCase(
        "jsonld_wrong_item",
        {"jsonld": [{"@type": "Thing"}, 1]},
        False,
        "declares metadata key 'jsonld[1]' as 'int', expected a mapping",
    ),
)


@dataclass(frozen=True, slots=True)
class MetadataMergeCase:
    """One chain of raw segments and the folded values it must settle on."""

    id: str
    segments_raw: tuple[object, ...]
    expected_title: str | None
    expected_fields: dict[str, object] = field(default_factory=dict)


METADATA_MERGE_CASES: tuple[MetadataMergeCase, ...] = (
    MetadataMergeCase("empty_chain", (), None),
    MetadataMergeCase("single_text", ({"title": "Wallet"},), "Wallet"),
    MetadataMergeCase(
        "template_wraps_child_text",
        (
            {"title": {"template": "{title} · {site_name}"}, "site_name": "Acme"},
            {"title": "Wallet"},
        ),
        "Wallet · Acme",
    ),
    MetadataMergeCase(
        "root_default_stands_bare",
        ({"title": {"template": "{title} · Acme", "default": "Acme"}},),
        "Acme",
    ),
    MetadataMergeCase(
        "default_survives_a_child_without_title",
        (
            {"title": {"template": "{title} · Acme", "default": "Acme"}},
            {"description": "Money"},
        ),
        "Acme",
        {"description": "Money"},
    ),
    MetadataMergeCase(
        "absolute_ignores_template",
        (
            {"title": {"template": "{title} · Acme"}},
            {"title": {"absolute": "Just this"}},
        ),
        "Just this",
    ),
    MetadataMergeCase(
        "nearer_template_replaces_the_root_one",
        (
            {"title": {"template": "{title} · Acme"}},
            {"title": {"template": "{title} | Wallet"}},
            {"title": "Cards"},
        ),
        "Cards | Wallet",
    ),
    MetadataMergeCase(
        "template_applies_to_the_segment_after_it",
        (
            {"title": {"template": "{title} · Acme", "default": "Acme"}},
            {"title": {"template": "{title} | Wallet"}},
        ),
        "Acme",
    ),
    MetadataMergeCase(
        "template_without_title_still_applies",
        ({"title": {"template": "Only Acme"}}, {"title": "Cards"}),
        "Only Acme",
    ),
    MetadataMergeCase(
        "site_name_missing_skips_the_template",
        ({"title": {"template": "{title} · {site_name}"}}, {"title": "Cards"}),
        "Cards",
    ),
    MetadataMergeCase(
        "site_name_reads_the_final_fold",
        (
            {"title": {"template": "{title} · {site_name}"}, "site_name": "Root"},
            {"title": "Cards", "site_name": "Leaf"},
        ),
        "Cards · Leaf",
        {"site_name": "Leaf"},
    ),
    MetadataMergeCase(
        "site_name_declared_after_the_template",
        (
            {"title": {"template": "{title} · {site_name}"}},
            {"title": "Cards", "site_name": "Leaf"},
        ),
        "Cards · Leaf",
    ),
    MetadataMergeCase(
        "later_segment_without_title_keeps_it",
        ({"title": "Wallet"}, {"description": "Money"}),
        "Wallet",
        {"description": "Money"},
    ),
    MetadataMergeCase(
        "nested_block_is_replaced_whole",
        ({"og": {"type": "website", "title": "Root"}}, {"og": {"title": "Leaf"}}),
        None,
        {"og": OpenGraph(title="Leaf")},
    ),
    MetadataMergeCase(
        "robots_string_replaces_the_dict",
        ({"robots": {"index": False}}, {"robots": "all"}),
        None,
        {"robots": "all"},
    ),
    MetadataMergeCase(
        "other_and_jsonld_are_replaced_whole",
        (
            {"other": {"a": "1", "b": "2"}, "jsonld": {"@type": "Root"}},
            {"other": {"c": "3"}, "jsonld": [{"@type": "Leaf"}]},
        ),
        None,
        {"other": (("c", "3"),), "jsonld": ({"@type": "Leaf"},)},
    ),
    MetadataMergeCase(
        "empty_other_does_not_clear_the_parent",
        ({"other": {"a": "1"}}, {"other": {}}),
        None,
        {"other": (("a", "1"),)},
    ),
    MetadataMergeCase(
        "scalars_fold_by_nearest",
        (
            {"description": "Root", "base": "https://a.example", "canonical": True},
            {"description": "Leaf", "canonical": "/leaf/"},
        ),
        None,
        {"description": "Leaf", "base": "https://a.example", "canonical": "/leaf/"},
    ),
)


@dataclass(frozen=True, slots=True)
class TemplateCase:
    """One title template and either its substitution or the error it raises."""

    id: str
    template: str
    values: dict[str, object] = field(default_factory=dict)
    expected: str | None = None
    error_fragment: str | None = None


TEMPLATE_CASES: tuple[TemplateCase, ...] = (
    TemplateCase(
        "both_placeholders",
        "{title} · {site_name}",
        {"title": "Wallet", "site_name": "Acme"},
        expected="Wallet · Acme",
    ),
    TemplateCase("plain_text", "Acme", {"title": "Wallet"}, expected="Acme"),
    TemplateCase(
        "escaped_braces",
        "{{title}} {title}",
        {"title": "Wallet"},
        expected="{title} Wallet",
    ),
    TemplateCase(
        "site_name_alone",
        "{site_name}",
        {"title": "Wallet", "site_name": "Acme"},
        expected="Acme",
    ),
    TemplateCase(
        "attribute_access",
        "{x.__class__}",
        error_fragment="names the placeholder 'x.__class__', expected one of "
        "site_name, title",
    ),
    TemplateCase(
        "index_access", "{c[0]}", error_fragment="names the placeholder 'c[0]'"
    ),
    TemplateCase("positional", "{0}", error_fragment="names the placeholder '0'"),
    TemplateCase("auto_numbered", "{}", error_fragment="names the placeholder ''"),
    TemplateCase(
        "conversion",
        "{title!r}",
        error_fragment="formats the placeholder 'title', expected a bare {title}",
    ),
    TemplateCase(
        "format_spec",
        "{title:>10}",
        error_fragment="formats the placeholder 'title', expected a bare {title}",
    ),
    TemplateCase(
        "unbalanced_open",
        "{title",
        error_fragment="is malformed, expected '}' before end of string",
    ),
    TemplateCase(
        "unbalanced_close",
        "}",
        error_fragment="is malformed, Single '}' encountered in format string",
    ),
    TemplateCase(
        "unknown_name",
        "{nope}",
        error_fragment="names the placeholder 'nope', expected one of site_name, title",
    ),
    TemplateCase(
        "missing_value",
        "{title} · {site_name}",
        {"title": "Wallet"},
        error_fragment="needs a value for 'site_name', which the chain did not provide",
    ),
)


@dataclass(frozen=True, slots=True)
class RobotsCase:
    """One robots value and the content its meta folds to."""

    id: str
    robots: Robots | str
    expected: str


ROBOTS_CASES: tuple[RobotsCase, ...] = (
    RobotsCase("string_as_is", "noindex, nofollow", "noindex, nofollow"),
    RobotsCase("empty", Robots(), ""),
    RobotsCase("index_follow", Robots(index=True, follow=True), "index, follow"),
    RobotsCase("noindex", Robots(index=False), "noindex"),
    RobotsCase("nofollow", Robots(follow=False), "nofollow"),
    RobotsCase(
        "flags",
        Robots(noarchive=True, nosnippet=True, noimageindex=True, notranslate=True),
        "noarchive, nosnippet, noimageindex, notranslate",
    ),
    RobotsCase("false_flags_stay_out", Robots(noarchive=False, nosnippet=False), ""),
    RobotsCase(
        "limits",
        Robots(
            unavailable_after="2026-12-31",
            max_snippet=20,
            max_image_preview="large",
            max_video_preview=-1,
        ),
        "unavailable_after: 2026-12-31, max-snippet:20, max-image-preview:large, "
        "max-video-preview:-1",
    ),
    RobotsCase(
        "everything_in_order",
        Robots(index=False, follow=True, noarchive=True, max_snippet=0),
        "noindex, follow, noarchive, max-snippet:0",
    ),
)


@dataclass(frozen=True, slots=True)
class AbsoluteUrlCase:
    """One URL to make absolute, the base and request path it sees, and the outcome.

    A `path` of `None` means no request at all, and `error` names the exception the
    resolution raises instead of answering `expected`.
    """

    id: str
    url: str
    base: str | None = None
    path: str | None = None
    expected: str | None = None
    error: type[Exception] | None = None


ABSOLUTE_URL_CASES: tuple[AbsoluteUrlCase, ...] = (
    AbsoluteUrlCase(
        "http_as_is", "http://x.example/a/", expected="http://x.example/a/"
    ),
    AbsoluteUrlCase(
        "https_as_is",
        "https://x.example/a/?q=1",
        base="https://acme.example",
        path="/p/",
        expected="https://x.example/a/?q=1",
    ),
    AbsoluteUrlCase(
        "root_relative_base_first",
        "/a/",
        base="https://acme.example/",
        path="/p/",
        expected="https://acme.example/a/",
    ),
    AbsoluteUrlCase(
        "root_relative_request", "/a/", path="/p/", expected="http://testserver/a/"
    ),
    AbsoluteUrlCase("root_relative_nothing", "/a/", error=PageMetadataURLError),
    AbsoluteUrlCase(
        "dot_relative",
        "./a",
        base="https://acme.example",
        path="/p/q/",
        expected="https://acme.example/p/q/a",
    ),
    AbsoluteUrlCase(
        "bare_relative", "a", path="/p/q/", expected="http://testserver/p/q/a"
    ),
    AbsoluteUrlCase(
        "parent_relative",
        "../a",
        base="https://acme.example",
        path="/p/q/",
        expected="https://acme.example/p/a",
    ),
    AbsoluteUrlCase(
        "relative_without_request",
        "./a",
        base="https://acme.example",
        error=PageMetadataURLError,
    ),
    AbsoluteUrlCase(
        "javascript_scheme", "javascript:alert(1)", error=PageMetadataShapeError
    ),
    AbsoluteUrlCase(
        "data_scheme",
        "data:text/html,x",
        base="https://acme.example",
        path="/p/",
        error=PageMetadataShapeError,
    ),
    AbsoluteUrlCase(
        "ftp_scheme", "ftp://x.example/a", path="/p/", error=PageMetadataShapeError
    ),
)
