from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tests.support.helpers import file_router_config_entry


if TYPE_CHECKING:
    from collections.abc import Callable


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
