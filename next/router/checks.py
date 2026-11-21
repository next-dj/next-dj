"""System checks for the `ROUTER` scope, its seams, layouts and page templates."""

from __future__ import annotations

import ast
import inspect
import re
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.urls import (
    LocalePrefixPattern,
    URLPattern,
    URLResolver,
    get_resolver,
    get_script_prefix,
)
from django.utils.module_loading import import_string

from next.checks import NEXT
from next.checks.common import raw_scope
from next.conf.checks import error_for_unusable_seam
from next.conf.router import (
    CURRENT,
    SCOPE,
    misfits,
    router_enabled,
    router_settings,
    speculation_problem,
)
from next.pages.checks.contexts import load_routed_pages
from next.pages.checks.reads import chrome_contents, chrome_names
from next.pages.gating import step_levels
from next.pages.loaders import (
    PAGE_MODULE,
    layout_chain,
    load_page_module,
    load_python_module_memo,
    serves,
    shared_layouts,
    template_source,
)
from next.pages.manager import page
from next.pages.navigation import router_declarations
from next.pages.placeholder import placeholder_spans, slot_problem
from next.pages.processors import context_processors
from next.pages.responses import cache_control
from next.pages.watch import (
    get_layout_djx_paths_for_watch,
    get_pages_directories_for_watch,
    get_template_djx_paths_for_watch,
)
from next.utils import is_within

from .backends import ChromeDigest, NavigationCut
from .current import CurrentLinks
from .manager import asset_prefixes


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator
    from pathlib import Path

    from next.conf.router import Misfit


_PREFIX: Final = f"NEXT_FRAMEWORK[{SCOPE!r}]"
_PATH_READS: Final = re.compile(r"\brequest\.(?:path|path_info|get_full_path)\b")
_PATH_SOURCES: Final = ("request.path", "get_full_path", "resolver_match")
_PATH_ATTRS: Final = frozenset(
    {
        "path",
        "path_info",
        "get_full_path",
        "get_full_path_info",
        "build_absolute_uri",
        "resolver_match",
        "META",
    }
)
_INLINE_SCRIPT: Final = re.compile(r"<script\b(?P<attrs>[^>]*)>", re.IGNORECASE)
_SRC: Final = re.compile(r"\ssrc\s*=", re.IGNORECASE)
_NONCE: Final = re.compile(r"\snonce\s*=", re.IGNORECASE)
_TYPE: Final = re.compile(r"""\stype\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)
_SCRIPT_TYPES: Final = frozenset(
    {"", "text/javascript", "application/javascript", "module"}
)


def _misfit_error(misfit: Misfit) -> CheckMessage:
    """Build the `next.E236` error for one `ROUTER` key or value outside its shape."""
    where = _PREFIX + "".join(f"[{key!r}]" for key in misfit.path)
    if misfit.unknown:
        shown = ", ".join(repr(key) for key in misfit.unknown)
        message = f"{where} has unknown keys {shown}"
    else:
        message = f"{where} must be {misfit.expected}, got {misfit.found}"
    return Error(
        f"{message}. The framework ignores it and keeps the default.",
        hint="Write the value in the shape named, or drop the key for its default.",
        obj=settings,
        id="next.E236",
    )


@register(NEXT)
def check_router_settings(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Validate the keys and the value shapes of the `ROUTER` scope (`next.E236`)."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    return [_misfit_error(misfit) for misfit in misfits(scope)]


def _class_problem(dotted: str, base: type) -> str | None:
    """Return why `dotted` names no usable subclass of `base`, `None` when it does."""
    try:
        target = import_string(dotted)
    except ImportError as exc:
        return f"does not import ({exc})"
    if not isinstance(target, type):
        return "names no class"
    if not issubclass(target, base):
        return f"is no subclass of {base.__name__}"
    if inspect.isabstract(target):
        return "is an abstract class"
    return None


_NAVIGATION_FAILS: Final = "every navigation raises ImproperlyConfigured"


def _seams(scope: Mapping[str, object]) -> Iterator[tuple[str, object, type, str]]:
    """Yield the address, the written value, the base and the cost of each seam."""
    yield f"{_PREFIX}['CUT']", scope.get("CUT"), NavigationCut, _NAVIGATION_FAILS
    yield f"{_PREFIX}['DIGEST']", scope.get("DIGEST"), ChromeDigest, _NAVIGATION_FAILS
    current = scope.get(CURRENT)
    if isinstance(current, dict):
        where = f"{_PREFIX}[{CURRENT!r}]['BACKEND']"
        cost = "the active-link pass raises ImproperlyConfigured"
        yield where, current.get("BACKEND"), CurrentLinks, cost


@register(NEXT)
def check_router_seams(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a router backend path that names no usable class (`next.E237`)."""
    scope = raw_scope(SCOPE)
    if scope is None:
        return []
    errors: list[CheckMessage] = []
    for where, dotted, base, cost in _seams(scope):
        if not isinstance(dotted, str) or not dotted:
            continue
        problem = _class_problem(dotted, base)
        if problem is not None:
            errors.append(
                error_for_unusable_seam(
                    where,
                    dotted,
                    problem,
                    cost,
                    f"Name a concrete subclass of {base.__name__}.",
                )
            )
    return errors


def _layouts() -> list[Path]:
    """Return every `layout.djx` of the page trees and the `DIRS` roots, sorted."""
    return sorted({*get_layout_djx_paths_for_watch(), *shared_layouts()})


@register(NEXT)
def check_layout_slots(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a layout placeholder the slot markers cannot wrap (`next.E239`)."""
    if not router_enabled():
        return []
    errors: list[CheckMessage] = []
    for layout in _layouts():
        text = template_source(layout)
        problem = None if text is None else slot_problem(text)
        if problem is not None:
            errors.append(
                Error(
                    f"The {{% template %}} placeholder of {layout} sits {problem}, "
                    "where the client router cannot mark the slot, so every "
                    "navigation under it loads hard or morphs the whole body.",
                    hint="Move the placeholder into the body of an element.",
                    obj=str(layout),
                    id="next.E239",
                )
            )
    return errors


def _request_name(tree: ast.AST) -> str | None:
    """Return the parameter the request reaches the first function of `tree` through."""
    func = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ),
        None,
    )
    if func is None:
        return None
    positional = [*func.args.posonlyargs, *func.args.args]
    if positional:
        return positional[0].arg
    return None if func.args.vararg is None else func.args.vararg.arg


def _bare(node: ast.AST, request: str | None) -> bool:
    """Whether `node` hands the request on whole or calls a method of it.

    Only a plain attribute such as `request.user` is known to leave the path behind.
    """
    called = {id(child.func) for child in ast.walk(node) if isinstance(child, ast.Call)}
    plain = {
        id(child.value)
        for child in ast.walk(node)
        if isinstance(child, ast.Attribute) and id(child) not in called
    }
    return any(
        isinstance(child, ast.Name) and child.id == request and id(child) not in plain
        for child in ast.walk(node)
    )


@dataclass(frozen=True, slots=True)
class _Locals:
    """The names a processor binds, by the value each plain assignment gives it.

    A name bound any other way, a loop target or an unpacking, is read as opaque.
    """

    assigned: dict[str, list[ast.expr]]
    opaque: frozenset[str]


def _locals(tree: ast.AST) -> _Locals:
    """Return the plain assignments of `tree` and every name it binds otherwise."""
    assigned: dict[str, list[ast.expr]] = {}
    plain: set[int] = set()
    for node in ast.walk(tree):
        pairs: list[tuple[ast.Name, ast.expr | None]]
        if isinstance(node, ast.Assign):
            pairs = [
                (target, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
        elif isinstance(node, ast.AnnAssign | ast.NamedExpr) and isinstance(
            node.target, ast.Name
        ):
            pairs = [(node.target, node.value)]
        else:
            continue
        for target, value in pairs:
            plain.add(id(target))
            if value is not None:
                assigned.setdefault(target.id, []).append(value)
    opaque = frozenset(
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name)
        and isinstance(node.ctx, ast.Store)
        and id(node) not in plain
    )
    return _Locals(assigned, opaque)


class _PathTaint:
    """Decide which expressions of one processor can change with the request path."""

    def __init__(self, tree: ast.AST) -> None:
        """Read the request parameter, then mark every local the path flows into."""
        self.request = _request_name(tree)
        bound = _locals(tree)
        self.tainted: set[str] = set(bound.opaque)
        grown = True
        while grown:
            grown = False
            for name, values in bound.assigned.items():
                if name not in self.tainted and any(map(self.depends, values)):
                    self.tainted.add(name)
                    grown = True

    def depends(self, node: ast.AST) -> bool:
        """Whether `node` reads a path attribute, a tainted name or the bare request."""
        for child in ast.walk(node):
            if isinstance(child, ast.Attribute) and child.attr in _PATH_ATTRS:
                root = _root(child.value)
                if not isinstance(root, ast.Name) or self.request in {None, root.id}:
                    return True
            if isinstance(child, ast.Name) and child.id in self.tainted:
                return True
        return _bare(node, self.request)


def _root(node: ast.expr) -> ast.expr:
    """Return the expression an attribute or subscript chain starts from."""
    while isinstance(node, ast.Attribute | ast.Subscript):
        node = node.value
    return node


def _path_dependent_keys(func: Callable[..., object]) -> frozenset[str]:
    """Return the keys of the dict literals `func` returns whose value reads the path.

    The source is read statically, so a value it cannot follow counts as dependent.
    """
    try:
        source = textwrap.dedent(inspect.getsource(func))
    except (OSError, TypeError):
        return frozenset()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return frozenset()
    taint = _PathTaint(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            found.update(
                key.value
                for key, value in zip(node.value.keys, node.value.values, strict=True)
                if isinstance(key, ast.Constant)
                and isinstance(key.value, str)
                and taint.depends(value)
            )
    return frozenset(found)


def _path_keys() -> frozenset[str]:
    """Return the keys whose value a context processor reading the path derives from it.

    A processor whose source never reads the path is skipped, so `messages` stays out.
    """
    keys: set[str] = set()
    for processor in context_processors():
        try:
            source = inspect.getsource(processor)
        except (OSError, TypeError):
            continue
        if any(needle in source for needle in _PATH_SOURCES):
            keys.update(_path_dependent_keys(processor))
    return frozenset(keys)


def _root_layouts() -> Iterator[Path]:
    """Yield every layout that ends the chain of a page below it."""
    for layout in _layouts():
        chain = layout_chain(layout.parent / PAGE_MODULE)
        if chain and chain[-1].resolve() == layout.resolve():
            yield layout


def _path_reads(text: str, keys: frozenset[str]) -> list[str]:
    """Return what a template reads outside its zones that changes with the path."""
    found: dict[str, None] = {}
    pattern = (
        re.compile(rf"\b({'|'.join(map(re.escape, sorted(keys)))})\b") if keys else None
    )
    for contents in chrome_contents(text):
        for match in _PATH_READS.finditer(contents):
            found[match.group(0)] = None
        if pattern is not None:
            for match in pattern.finditer(contents):
                found[match.group(1)] = None
    return list(found)


@register(NEXT)
def check_root_layout_paths(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a root layout whose chrome reads the request path (`next.W176`)."""
    if not router_enabled():
        return []
    keys = _path_keys()
    warnings: list[CheckMessage] = []
    for layout in _root_layouts():
        text = template_source(layout)
        reads = [] if text is None else _path_reads(text, keys)
        if reads:
            warnings.append(
                DjangoWarning(
                    f"The root layout {layout} reads {', '.join(reads)} outside a "
                    "zone, so its chrome differs on every page and every navigation "
                    "under it morphs the whole body.",
                    hint="Move the value into a {% zone %} or into the page.",
                    obj=str(layout),
                    id="next.W176",
                )
            )
    return warnings


def _distance(layout: Path, page_path: Path) -> int | None:
    """Return how many directories `layout` sits above the page, `None` off its path."""
    folder = layout.parent
    start = page_path.parent
    if not is_within(start, folder):
        return None
    return len(start.parts) - len(folder.parts)


def _varying_keys(page_path: Path, distance: int | None) -> frozenset[str]:
    """Return the keys a page takes from callables below a layout `distance` up.

    A callable of the layout's own directory or above feeds every page below it alike.
    """
    contexts = page.contexts
    graph = contexts.graph(page_path)
    levels = step_levels(graph, contexts.inherited_levels(page_path))
    return frozenset(
        step.key
        for step, level in zip(graph.steps, levels, strict=True)
        if step.key is not None and (distance is None or level < distance)
    )


def _kept_names(layout: Path) -> frozenset[str]:
    """Return what `layout` reads outside its zones and slot, none without a slot."""
    text = template_source(layout)
    if text is None or not placeholder_spans(text):
        return frozenset()
    return chrome_names(text)


def _page_reads() -> dict[Path, dict[str, Path]]:
    """Map each layout to the page keys its chrome reads, with a page that sets each."""
    kept: dict[Path, frozenset[str]] = {}
    found: dict[Path, dict[str, Path]] = {}
    for _url, page_path in load_routed_pages() or []:
        if not serves(page_path, *load_page_module(page_path)):
            continue
        for layout in layout_chain(page_path):
            names = kept.get(layout)
            if names is None:
                names = kept[layout] = _kept_names(layout)
            if not names:
                continue
            keys = names & _varying_keys(page_path, _distance(layout, page_path))
            for key in sorted(keys):
                found.setdefault(layout, {}).setdefault(key, page_path)
    return found


@register(NEXT)
def check_layout_page_reads(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a layout chrome reading a key the pages below set (`next.W183`)."""
    if not router_enabled():
        return []
    warnings: list[CheckMessage] = []
    for layout, keys in sorted(_page_reads().items()):
        shown = ", ".join(repr(key) for key in sorted(keys))
        first = min(keys)
        warnings.append(
            DjangoWarning(
                f"The layout {layout} reads {shown} outside a zone, which a @context "
                f"callable of a page below it provides, as {keys[first]} does for "
                f"{first!r}, so its chrome differs between pages that set it "
                "differently and the client router cannot keep the layout across "
                "a navigation between them.",
                hint="Wrap the part that reads it in a {% zone %}, which a kept "
                "layout morphs on its own, or move it into the page slot.",
                obj=str(layout),
                id="next.W183",
            )
        )
    return warnings


def _inline_scripts(text: str) -> int:
    """Return how many executable inline `<script>` tags of `text` carry no nonce."""
    count = 0
    for match in _INLINE_SCRIPT.finditer(text):
        attrs = match["attrs"]
        kind = _TYPE.search(attrs)
        if kind is not None and kind.group(1).lower() not in _SCRIPT_TYPES:
            continue
        if _SRC.search(attrs) is None and _NONCE.search(attrs) is None:
            count += 1
    return count


def _templates() -> Iterable[Path]:
    """Return every page and layout template of the page trees, sorted."""
    return sorted({*get_template_djx_paths_for_watch(), *_layouts()})


@register(NEXT)
def check_inline_scripts(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about an inline script without a nonce in a template (`next.W177`)."""
    if not router_enabled():
        return []
    warnings: list[CheckMessage] = []
    for path in _templates():
        text = template_source(path)
        if text is not None and _inline_scripts(text):
            warnings.append(
                DjangoWarning(
                    f"{path} holds an inline <script> without a nonce, which does "
                    "not run after a client router navigation inserts it.",
                    hint="The router runs an inline script only when its nonce is "
                    'the response nonce, so add nonce="{{ csp_nonce }}" or '
                    'nonce="{{ request.csp_nonce }}" under a CSP middleware, or move '
                    "the code into a co-located script.",
                    obj=str(path),
                    id="next.W177",
                )
            )
    return warnings


def _page_modules() -> Iterator[Path]:
    """Yield every `page.py` of the page trees."""
    for root in get_pages_directories_for_watch():
        yield from sorted(root.rglob(PAGE_MODULE))


@register(NEXT)
def check_no_store_pages(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a page whose cache forbids every store (`next.W178`)."""
    if not router_enabled():
        return []
    warnings: list[CheckMessage] = []
    for file_path in _page_modules():
        module = load_python_module_memo(file_path)
        control = cache_control(getattr(module, "cache", None))
        if control is not None and not control.stores:
            warnings.append(
                DjangoWarning(
                    f"{file_path} declares Cache-Control: no-store, so the browser "
                    "keeps no back-forward copy of the page the client router "
                    "restores from.",
                    hint="Prefer no_cache, which revalidates and keeps the copy.",
                    obj=str(file_path),
                    id="next.W178",
                )
            )
    return warnings


_SPECULATION: Final = "SPECULATION"


@register(NEXT)
def check_speculation(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `SPECULATION` value that emits no rules (`next.E242`)."""
    scope = raw_scope(SCOPE)
    value = None if scope is None else scope.get(_SPECULATION)
    if not isinstance(value, str | Mapping):
        return []
    problem = speculation_problem(value)
    if problem is None:
        return []
    return [
        Error(
            f"{_PREFIX}[{_SPECULATION!r}] {problem}, so documents carry no "
            "Speculation Rules.",
            hint="Name the eagerness 'conservative', 'moderate' or 'eager', as a "
            "string or under 'eagerness'. Only prefetch rules are emitted.",
            obj=settings,
            id="next.E242",
        )
    ]


@register(NEXT)
def check_router_declarations(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Report a `page.router()` call that applies nothing (`next.E238`)."""
    errors: list[CheckMessage] = [
        Error(
            f"page.router() in {file_path} declares nothing, since {problem}. The "
            "page keeps the default router options.",
            hint="Call page.router(navigate=..., prefetch=..., snapshot=...) with "
            "bools at the top of the module.",
            obj=settings,
            id="next.E238",
        )
        for file_path, declaration in router_declarations.loaded()
        if (problem := declaration.problem()) is not None
    ]
    errors.extend(
        Error(
            f"page.router() in {file_path} declares nothing, since the call sits "
            "outside a page.py and so names no page.",
            hint="Call page.router() at the top of the page.py of the page it is for.",
            obj=settings,
            id="next.E238",
        )
        for file_path in router_declarations.outside()
    )
    return errors


_REGEX_META: Final = frozenset(".^$*+?{}[]()|")
_QUANTIFIERS: Final = frozenset("?*{")
_ENDS: Final = frozenset({"$", r"\Z"})


def _regex_head(regex: str) -> tuple[str, str]:
    """Return the literal start of a URL pattern regex and the rest after it."""
    if "|" in regex:
        return "", regex
    text = regex.removeprefix("^")
    head: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        escaped = char == "\\" and not text[index + 1 : index + 2].isalnum()
        if char in _REGEX_META or (char == "\\" and not escaped):
            break
        step = 2 if escaped else 1
        if text[index + step : index + step + 1] in _QUANTIFIERS:
            break
        head.append(text[index + step - 1])
        index += step
    return "".join(head), text[index:]


def _pattern_heads(entry: URLPattern | URLResolver) -> Iterator[tuple[str, bool]]:
    """Yield each literal start of an entry and whether the entry is that literal.

    A language prefix stands for every configured language and for none.
    """
    pattern = entry.pattern
    if isinstance(pattern, LocalePrefixPattern):
        yield "", True
        for code, _name in settings.LANGUAGES:
            yield f"{code}/", True
        return
    head, rest = _regex_head(pattern.regex.pattern)
    yield head, rest in _ENDS if isinstance(entry, URLPattern) else not rest


def _route_heads(
    entries: Iterable[URLPattern | URLResolver], above: str = ""
) -> Iterator[tuple[str, bool]]:
    """Yield the literal start of every route and whether a path may extend it."""
    for entry in entries:
        for head, whole in _pattern_heads(entry):
            if isinstance(entry, URLResolver) and whole:
                yield from _route_heads(entry.url_patterns, above + head)
            else:
                yield above + head, not whole


def _serves_under(rest: str, heads: Iterable[tuple[str, bool]]) -> bool:
    """Return whether some route may serve a path that starts with `rest`."""
    return any(
        head.startswith(rest) or (extends and rest.startswith(head))
        for head, extends in heads
    )


@register(Tags.urls, NEXT)
def check_exclude_prefixes(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a `ROUTER["EXCLUDE"]` prefix no route serves (`next.W184`).

    A converter or a regex group past the literal start of a route serves anything.
    """
    written = router_settings().exclude
    if not router_enabled() or not written:
        return []
    script = get_script_prefix()
    assets = asset_prefixes()
    heads = list(_route_heads(get_resolver().url_patterns))
    return [
        DjangoWarning(
            f"{_PREFIX}['EXCLUDE'] holds {prefix!r}, which starts no path the URLconf "
            "serves, so it keeps nothing out of the client router.",
            hint="Correct the prefix to the path of a route, or drop it. Silence "
            "next.W184 for a prefix that a server outside Django answers.",
            obj=settings,
            id="next.W184",
        )
        for prefix in written
        if prefix.startswith(script)
        and not any(prefix.startswith(a) or a.startswith(prefix) for a in assets)
        and not _serves_under(prefix.removeprefix(script), heads)
    ]


__all__ = [
    "check_exclude_prefixes",
    "check_inline_scripts",
    "check_layout_page_reads",
    "check_layout_slots",
    "check_no_store_pages",
    "check_root_layout_paths",
    "check_router_declarations",
    "check_router_seams",
    "check_router_settings",
    "check_speculation",
]
