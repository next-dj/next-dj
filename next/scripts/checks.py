"""System checks for the `scripts.py` sources."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.staticfiles import finders
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT
from next.conf import next_framework_settings
from next.consent.checks import category_list_problem
from next.consent.manager import consent_categories, consent_configured, server_render
from next.pages.checks.composed import iter_composed_pages
from next.pages.checks.metadata.templates import TemplateSearch, page_template_path
from next.static import ScriptInjectionPolicy
from next.static.assets import static_name
from next.static.errors import StaticAssetTraversalError

from .manager import scripts_manager
from .markers import HEAD_STRATEGIES, Script, Strategy, allowed_attr
from .nodes import ConsentedTagNode
from .render import inline_body


if TYPE_CHECKING:
    from django.template.base import NodeList

    from .discovery import ScriptsSource


_NAMED_PAGES: Final = 3


def _sources() -> tuple[ScriptsSource, ...]:
    return scripts_manager.sources()


@register(NEXT)
def check_scripts_sources(*args, **kwargs) -> list[CheckMessage]:
    """Report a `scripts.py` that fails or declares no `Script` (`next.E133`)."""
    messages: list[CheckMessage] = []
    for source in _sources():
        if source.error is not None:
            messages.append(
                Error(
                    f"{source.error}: {source.error.__cause__!r}. Fix the error the "
                    "import raises. Until then the pages of the tree render without "
                    "these scripts.",
                    obj=str(source.path),
                    id="next.E133",
                )
            )
        elif source.problem is not None:
            messages.append(
                Error(
                    f"{source.path} declares {source.problem}, so its tree runs "
                    "only the Script values it holds. Write "
                    "scripts = (Script(...), ...).",
                    obj=str(source.path),
                    id="next.E133",
                )
            )
    return messages


@register(NEXT)
def check_script_names(*args, **kwargs) -> list[CheckMessage]:
    """Report a script name declared twice in one tree (`next.E134`)."""
    messages: list[CheckMessage] = []
    for source in _sources():
        seen: set[str] = set()
        for script in source.scripts:
            if script.name in seen:
                messages.append(
                    Error(
                        f"{source.path} declares the script {script.name!r} twice, "
                        "and {% script %} and the runtime tell scripts by name. "
                        "Rename one.",
                        obj=str(source.path),
                        id="next.E134",
                    )
                )
            seen.add(script.name)
    return messages


@register(NEXT)
def check_script_categories(*args, **kwargs) -> list[CheckMessage]:
    """Report a script in a category the list leaves out (`next.E140`).

    While `next.E135` reports the list itself, no script is reported against it.
    """
    if category_list_problem() is not None:
        return []
    categories = consent_categories()
    return [
        Error(
            f"{source.path} puts the script {script.name!r} in the category "
            f"{script.category!r}, which NEXT_FRAMEWORK['CONSENT']['CATEGORIES'] "
            f"does not list, so no visitor can grant it. Use one of "
            f"{', '.join(categories)}, or add {script.category!r} to the list.",
            obj=str(source.path),
            id="next.E140",
        )
        for source in _sources()
        for script in source.scripts
        if script.category not in categories
    ]


def _src_problem(src: str) -> str | None:
    """Return why `src` cannot load, `None` for an http(s) URL or a found name."""
    parts = urlsplit(src)
    if parts.scheme or parts.netloc:
        if parts.scheme in {"https", "http"} and parts.netloc:
            return None
        return "a URL that is not an absolute http or https URL"
    try:
        name = static_name(src)
    except StaticAssetTraversalError:
        return "a path outside the static root"
    if name is None:
        return "a path, not a staticfiles name"
    if finders.find(name):
        return None
    return "a staticfiles name no finder answers"


def _script_problems(script: Script) -> list[tuple[str, str]]:
    """Return what is wrong with one script, each problem with the id it earns."""
    problems: list[tuple[str, str]] = []
    if script.src is None and script.init is None:
        problems.append(("carries neither src nor init", "next.E136"))
    if script.src is not None:
        problem = _src_problem(script.src)
        if problem is not None:
            problems.append((f"loads {script.src!r}, {problem}", "next.E141"))
    if script.init is not None and inline_body(script.init) != script.init:
        problems.append(
            (
                (
                    "holds </script, <script or <!-- in its init, which the HTML "
                    "parser reads as markup, so the element closes early or hides "
                    "the rest of the page"
                ),
                "next.E142",
            )
        )
    strays = sorted(name for name in script.attrs if not allowed_attr(name))
    if strays:
        problems.append(
            (f"carries the attributes {', '.join(strays)} it may not set", "next.E143")
        )
    if script.strategy not in set(Strategy):
        problems.append((f"names the strategy {script.strategy!r}", "next.E144"))
    return problems


@register(NEXT)
def check_script_declarations(*args, **kwargs) -> list[CheckMessage]:
    """Report a script with nothing to run or a part it cannot carry as declared."""
    return [
        Error(
            f"{source.path} declares the script {script.name!r}, which {problem}.",
            obj=str(source.path),
            id=check_id,
        )
        for source in _sources()
        for script in source.scripts
        for problem, check_id in _script_problems(script)
    ]


def _needs_runtime(script: Script) -> bool:
    return script.gated or script.strategy not in HEAD_STRATEGIES


@register(NEXT)
def check_scripts_need_the_runtime(*args, **kwargs) -> list[CheckMessage]:
    """Report scripts only the runtime loads while it is not injected (`next.E138`)."""
    options = next_framework_settings.NEXT_JS_OPTIONS
    policy = options.get("policy") if isinstance(options, Mapping) else None
    if policy not in {ScriptInjectionPolicy.DISABLED, "disabled"}:
        return []
    return [
        Error(
            f"{source.path} declares the script {script.name!r}, which only the "
            "runtime loads, while NEXT_JS_OPTIONS['policy'] keeps the runtime out "
            "of every page. It never runs.",
            obj=str(source.path),
            id="next.E138",
        )
        for source in _sources()
        for script in source.scripts
        if _needs_runtime(script)
    ]


@register(NEXT)
def check_gated_blocking_scripts(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a gated blocking script consent renders on the client (W116)."""
    if server_render() is True:
        return []
    return [
        DjangoWarning(
            f"{source.path} declares the blocking script {script.name!r} in the "
            f"category {script.category!r}. On a page the runtime renders consent "
            "for, it loads after the page, so it blocks nothing. Declare it "
            "Strategy.ASYNC or Strategy.DEFER, or set "
            "NEXT_FRAMEWORK['CONSENT']['SERVER_RENDER'] to True.",
            obj=str(source.path),
            id="next.W116",
        )
        for source in _sources()
        for script in source.scripts
        if script.gated and script.strategy == Strategy.BLOCKING
    ]


def _renders_consented(nodelist: NodeList) -> bool:
    return bool(nodelist.get_nodes_by_type(ConsentedTagNode))


@register(Tags.templates, NEXT)
def check_consented_needs_consent(*args, **kwargs) -> list[CheckMessage]:
    """Warn about `{% #consented %}` while `CONSENT` stays unset (`next.W123`).

    A partial response never carries `$consent`, so only the setting enables it.
    """
    if consent_configured():
        return []
    search = TemplateSearch(_renders_consented)
    pages = [
        str(page_path)
        for page_path, template in iter_composed_pages()
        if search.reaches(template.nodelist, page_template_path(page_path))
    ]
    if not pages:
        return []
    rest = len(pages) - _NAMED_PAGES
    named = ", ".join(pages[:_NAMED_PAGES]) + (f" and {rest} more" if rest > 0 else "")
    return [
        DjangoWarning(
            f"{{% #consented %}} renders on {named}, while NEXT_FRAMEWORK holds no "
            "'CONSENT' entry. A block that reaches a page only through a partial "
            "patch stays hidden there, since that entry is what turns consent on for "
            "every page. Add NEXT_FRAMEWORK['CONSENT'], an empty mapping at least.",
            obj=settings,
            id="next.W123",
        )
    ]


@register(Tags.templates, NEXT)
def check_consented_categories(*args, **kwargs) -> list[CheckMessage]:
    """Warn about `{% #consented %}` naming an unlisted category (`next.W091`).

    Only a literal name is read, and the message names the first page that renders it.
    """
    if not consent_configured() or category_list_problem() is not None:
        return []
    categories = consent_categories()
    unknown: dict[str, str] = {}
    page = ""

    def collect(nodelist: NodeList) -> bool:
        nodes = cast(
            "list[ConsentedTagNode]", nodelist.get_nodes_by_type(ConsentedTagNode)
        )
        for node in nodes:
            name = node.literal_category()
            if name is not None and name not in categories:
                unknown.setdefault(name, page)
        # No match is reported, so the search visits every template the page reaches.
        return False

    search = TemplateSearch(collect)
    for page_path, template in iter_composed_pages():
        page = str(page_path)
        search.reaches(template.nodelist, page_template_path(page_path))
    return [
        DjangoWarning(
            f"{{% #consented {name!r} %}} renders on {first}, while "
            "NEXT_FRAMEWORK['CONSENT']['CATEGORIES'] does not list "
            f"{name!r}, so no visitor can grant it and the block always renders "
            f"its else branch. Add {name!r} to the list, or name one of "
            f"{', '.join(categories)}.",
            obj=settings,
            id="next.W091",
        )
        for name, first in unknown.items()
    ]


@register(NEXT, deploy=True)
def check_script_deploy(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a script loaded over plain HTTP (`next.W118`)."""
    return [
        DjangoWarning(
            f"{source.path} loads the script {script.name!r} over plain HTTP, "
            "which an https page blocks as mixed content. Load its src over "
            "https://.",
            obj=str(source.path),
            id="next.W118",
        )
        for source in _sources()
        for script in source.scripts
        if script.src is not None and script.src.startswith("http://")
    ]


__all__ = [
    "check_consented_categories",
    "check_consented_needs_consent",
    "check_gated_blocking_scripts",
    "check_script_categories",
    "check_script_declarations",
    "check_script_deploy",
    "check_script_names",
    "check_scripts_need_the_runtime",
    "check_scripts_sources",
]
