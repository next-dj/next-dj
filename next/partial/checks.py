"""System checks for the partial-rendering subsystem.

This module is excluded from coverage like every other area `checks.py`.
The zone checks read the same compiled page templates the renderer uses,
so a misconfigured zone is caught at `manage.py check` time rather than
on a partial request.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast

from django.conf import settings
from django.contrib.staticfiles.storage import ManifestFilesMixin
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)
from django.template import TemplateDoesNotExist, TemplateSyntaxError
from django.template.base import Node, NodeList, TextNode
from django.template.defaulttags import ForNode, IfNode, WithNode

from next.checks import NEXT
from next.checks.common import (
    first_visit,
    get_components_manager,
    get_router_manager,
    iter_scanned_page_pairs,
)
from next.conf import import_class_cached, next_framework_settings
from next.conf.signals import settings_reloaded
from next.forms.backends import FormActionBackend
from next.forms.manager import form_action_manager
from next.pages import page
from next.templatetags.forms import FormNode

from .registry import BUILTIN_OPS, patch_op_registry
from .zone import ZoneNode


if TYPE_CHECKING:
    from collections.abc import Iterator

    from django.template.base import Template

    from next.pages.registry import ZoneBinding
    from next.urls import RouterBackend, RouterManager

    _ComposedMemo = tuple[RouterManager, list[tuple[Path, Template]]] | None


E_DUPLICATE_ZONE: Final = "next.E060"
E_NON_ASCII_ZONE: Final = "next.E061"
E_ZONE_IN_FOR: Final = "next.E062"
E_ZONE_IN_IF: Final = "next.E063"
E_LAZY_WITHOUT_PLACEHOLDER: Final = "next.E064"
E_ZONE_IN_COMPONENT: Final = "next.E065"
E_UNREGISTERED_OP: Final = "next.E066"
E_BACKENDS_NOT_A_LIST: Final = "next.E067"
E_COMPOSED_TEMPLATE_SYNTAX: Final = "next.E072"
E_BACKEND_WITHOUT_PATH: Final = "next.E073"
E_CONTEXT_ZONE_UNKNOWN: Final = "next.E078"

W_WITH_OVER_ZONE: Final = "next.W067"
W_FORM_BACKEND_NOT_AWARE: Final = "next.W068"
W_MANIFEST_VERSION_NO_STORAGE: Final = "next.W069"
W_FORM_IN_FOR_NO_KEY: Final = "next.W070"
W_TOO_MANY_BACKENDS: Final = "next.W071"

_PARTIAL_BACKENDS_KEY: Final = "PARTIAL_BACKENDS"
_MIN_DUPLICATE_COUNT: Final = 2
_FORM_TARGET_ATTR: Final = "data-next-target"
_FORM_KEY_ATTR: Final = "data-next-key"


_ZONE_SLUG = re.compile(r"\A[A-Za-z0-9_-]+\Z")


# One walk of the page tree shared by every zone check per run. Comparing the
# stored manager by identity invalidates the memo when the manager is rebuilt.
_COMPOSED_PAGES_MEMO: "dict[str, _ComposedMemo]" = {"value": None}


def _iter_composed_pages() -> "Iterator[tuple[Path, Template]]":
    """Yield each page path with its compiled composed template.

    Pages whose body is produced dynamically by `render()` have no
    static composed template and are skipped. A page that fails to
    compile is skipped here and reported by
    `check_composed_templates_compile`. The result is memoised per
    router-manager instance so all zone checks share one walk.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    memo = _COMPOSED_PAGES_MEMO["value"]
    if memo is not None and memo[0] is router_manager:
        yield from memo[1]
        return
    pages = list(_collect_composed_pages(router_manager))
    _COMPOSED_PAGES_MEMO["value"] = (router_manager, pages)
    yield from pages


def _collect_composed_pages(
    router_manager: "RouterManager",
) -> "Iterator[tuple[Path, Template]]":
    """Walk every router's scanned pages, de-duplicating by resolved path."""
    seen: set[Path] = set()
    for router in router_manager.backends:
        yield from _iter_router_pages(router, seen)


def reset_composed_pages_memo(**kwargs) -> None:
    """Drop the memoised composed-page list for the next check run.

    Identity against the router manager already invalidates the memo when the
    manager is rebuilt. Call this explicitly after editing a `.djx` in place
    under a live manager, since `settings_reloaded` only fires when
    `NEXT_FRAMEWORK` itself changes.
    """
    _COMPOSED_PAGES_MEMO["value"] = None


settings_reloaded.connect(reset_composed_pages_memo)


def _iter_router_pages(
    router: "RouterBackend", seen: set[Path]
) -> "Iterator[tuple[Path, Template]]":
    """Yield compiled composed templates for one router's scanned pages."""
    for _url_path, page_path in iter_scanned_page_pairs(router):
        if not first_visit(page_path, seen) or not page.has_template(page_path):
            continue
        try:
            template = page.composed_template_for(page_path)
        except (TemplateSyntaxError, TemplateDoesNotExist, OSError, ValueError):
            continue
        yield page_path, template


def _zone_nodes(template: "Template") -> list[ZoneNode]:
    """Return every zone node of a compiled template."""
    return cast("list[ZoneNode]", template.nodelist.get_nodes_by_type(ZoneNode))


def _child_nodelists(node: Node) -> "Iterator[NodeList]":
    """Yield each declared child node list of a node."""
    for attr in node.child_nodelists:
        nodelist = getattr(node, attr, None)
        if isinstance(nodelist, NodeList):
            yield nodelist


def _significant(nodelist: NodeList) -> list[Node]:
    """Return the nodes of a list that are not pure whitespace text."""
    out: list[Node] = []
    for node in nodelist:
        if isinstance(node, TextNode) and not node.s.strip():
            continue
        out.append(node)
    return out


@register(Tags.templates, NEXT)
def check_composed_templates_compile(*args, **kwargs) -> list[CheckMessage]:
    """Error when a composed page template fails to compile (`next.E072`).

    The zone checks skip a page whose composed template does not
    compile, so without this check the syntax error would surface only
    as a 500 on the first request to the page.
    """
    messages: list[CheckMessage] = []
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return messages
    seen: set[Path] = set()
    for router in router_manager.backends:
        for _url_path, page_path in iter_scanned_page_pairs(router):
            if not first_visit(page_path, seen) or not page.has_template(page_path):
                continue
            try:
                page.composed_template_for(page_path)
            except TemplateSyntaxError as error:
                messages.append(
                    Error(
                        f"The composed page template for {page_path} does not "
                        f"compile. {error}",
                        obj=str(page_path),
                        id=E_COMPOSED_TEMPLATE_SYNTAX,
                    )
                )
            except (TemplateDoesNotExist, OSError, ValueError):
                continue
    return messages


@register(Tags.templates, NEXT)
def check_duplicate_zone_names(*args, **kwargs) -> list[CheckMessage]:
    """Error when two zones in one composed page share a name (`next.E060`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        counts: dict[str, int] = {}
        for node in _zone_nodes(template):
            counts[node.name] = counts.get(node.name, 0) + 1
        for name, count in counts.items():
            if count < _MIN_DUPLICATE_COUNT:
                continue
            messages.append(
                Error(
                    f'Zone "{name}" is declared {count} times in the composed '
                    f"page template for {page_path}. Zone names address one node "
                    "each, so they must be unique across the layout chain and "
                    "the page body.",
                    obj=str(page_path),
                    id=E_DUPLICATE_ZONE,
                )
            )
    return messages


@register(Tags.templates, NEXT)
def check_zone_name_is_slug(*args, **kwargs) -> list[CheckMessage]:
    """Error when a zone name is not an ASCII slug (`next.E061`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        for node in _zone_nodes(template):
            if _ZONE_SLUG.match(node.name):
                continue
            messages.append(
                Error(
                    f'Zone name "{node.name}" in {page_path} is not an ASCII '
                    "slug. Zone names travel in the X-Next-Zone header, which "
                    "is latin-1. Use letters, digits, hyphens, or underscores.",
                    obj=str(page_path),
                    id=E_NON_ASCII_ZONE,
                )
            )
    return messages


@register(Tags.templates, NEXT)
def check_zone_not_in_loop(*args, **kwargs) -> list[CheckMessage]:
    """Error when a zone sits inside a `{% for %}` loop (`next.E062`)."""
    return _ancestor_check(
        ancestor=ForNode,
        check_id=E_ZONE_IN_FOR,
        reason=(
            "A standalone zone render does not see loop variables, so the zone "
            "cannot be re-rendered on its own. Move the zone out of the loop or "
            "wrap each item in a component."
        ),
    )


@register(Tags.templates, NEXT)
def check_zone_not_in_if(*args, **kwargs) -> list[CheckMessage]:
    """Error when a zone sits inside an `{% if %}` block (`next.E063`)."""
    return _ancestor_check(
        ancestor=IfNode,
        check_id=E_ZONE_IN_IF,
        reason=(
            "A standalone zone render does not evaluate the enclosing "
            "condition, so the zone's visibility cannot be honoured. Move the "
            "condition inside the zone body instead."
        ),
    )


def _ancestor_check(
    *, ancestor: type[Node], check_id: str, reason: str
) -> list[CheckMessage]:
    """Return errors for every zone nested under an `ancestor` node type."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        messages.extend(
            Error(
                f'Zone "{zone_name}" in {page_path} is nested inside a '
                f"{_TAG_LABELS[ancestor]} block. {reason}",
                obj=str(page_path),
                id=check_id,
            )
            for zone_name in _zones_under(template.nodelist, ancestor)
        )
    return messages


_TAG_LABELS: Final[dict[type[Node], str]] = {ForNode: "{% for %}", IfNode: "{% if %}"}


def _zones_under(
    nodelist: NodeList, ancestor: type[Node], *, inside: bool = False
) -> "Iterator[str]":
    """Yield names of zones reached while an `ancestor` node is on the path."""
    for node in nodelist:
        if isinstance(node, ZoneNode):
            if inside:
                yield node.name
            continue
        now_inside = inside or isinstance(node, ancestor)
        for child in _child_nodelists(node):
            yield from _zones_under(child, ancestor, inside=now_inside)


def _forms_in_loop(nodelist: NodeList, *, inside: bool = False) -> "Iterator[FormNode]":
    """Yield each `{% form %}` node reached while a `{% for %}` is on the path."""
    for node in nodelist:
        if isinstance(node, FormNode) and inside:
            yield node
        now_inside = inside or isinstance(node, ForNode)
        for child in _child_nodelists(node):
            yield from _forms_in_loop(child, inside=now_inside)


def _form_has_partial_attr(node: FormNode, attr: str) -> bool:
    """Return True when the form tag wrote the given `data-next-*` attribute."""
    return any(name == attr for name, _expr in node.partial_attrs)


@register(Tags.templates, NEXT)
def check_repeated_form_has_key(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a looped `{% form %}` has no key or zone (`next.W070`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        for node in _forms_in_loop(template.nodelist):
            if _form_has_partial_attr(node, _FORM_TARGET_ATTR):
                continue
            if _form_has_partial_attr(node, _FORM_KEY_ATTR):
                continue
            messages.append(
                DjangoWarning(
                    f"Form {node.action_expr} in {page_path} renders inside a "
                    "{% for %} loop without a key= or a zone=. Every iteration "
                    "shares one action uid, so a partial morph cannot tell the "
                    "instances apart and re-renders the wrong one. Add key= with "
                    "a stable per-row value, or a zone= that wraps the list.",
                    obj=str(page_path),
                    id=W_FORM_IN_FOR_NO_KEY,
                )
            )
    return messages


@register(Tags.templates, NEXT)
def check_lazy_zone_has_placeholder(*args, **kwargs) -> list[CheckMessage]:
    """Error when a lazy zone declares no `{% placeholder %}` (`next.E064`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        for node in _zone_nodes(template):
            if node.options.lazy is None or _significant(node.placeholder):
                continue
            messages.append(
                Error(
                    f'Lazy zone "{node.name}" in {page_path} has no '
                    "{% placeholder %} branch. A lazy zone shows its placeholder "
                    "until the body arrives, so the branch is required.",
                    obj=str(page_path),
                    id=E_LAZY_WITHOUT_PLACEHOLDER,
                )
            )
    return messages


@register(Tags.templates, NEXT)
def check_with_directly_over_zone(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a `{% with %}` wraps a zone directly (`next.W067`)."""
    messages: list[CheckMessage] = []
    for page_path, template in _iter_composed_pages():
        messages.extend(
            DjangoWarning(
                f'Zone "{zone_name}" in {page_path} sits directly inside a '
                "{% with %} block. The with-bindings are not visible to a "
                "standalone zone render. Move the bindings into a context "
                "provider or inside the zone body.",
                obj=str(page_path),
                id=W_WITH_OVER_ZONE,
            )
            for zone_name in _zones_directly_in_with(template.nodelist)
        )
    return messages


def _zones_directly_in_with(nodelist: NodeList) -> "Iterator[str]":
    """Yield zone names that are direct children of a `{% with %}` block."""
    for node in nodelist:
        if isinstance(node, WithNode):
            for child in _significant(node.nodelist):
                if isinstance(child, ZoneNode):
                    yield child.name
        for child_list in _child_nodelists(node):
            yield from _zones_directly_in_with(child_list)


@register(Tags.templates, NEXT)
def check_context_zone_names_exist(*args, **kwargs) -> list[CheckMessage]:
    """Error when a `@context(zone=)` names an undeclared zone (`next.E078`).

    A full render runs every callable, so a misspelt zone name looks
    healthy there and silently drops the callable from every zone
    request instead.
    """
    messages: list[CheckMessage] = []
    # The composed-template walk imports each `page.py`, so read bindings after.
    composed = list(_iter_composed_pages())
    bindings = page.zone_bindings()
    for page_path, template in composed:
        page_bindings = bindings.get(page_path, ())
        if not any(binding.zones for binding in page_bindings):
            continue
        declared = {node.name for node in _zone_nodes(template)}
        sentence = _declared_zones_sentence(sorted(declared))
        for label, zone_name in _zone_bound_contexts(page_bindings):
            if zone_name in declared:
                continue
            messages.append(
                Error(
                    f"The @context {label} in {page_path} binds to zone "
                    f'"{zone_name}", which the composed page template does not '
                    "declare. No zone request ever matches the callable, so its "
                    f"value is missing from every zone render. {sentence}",
                    obj=str(page_path),
                    id=E_CONTEXT_ZONE_UNKNOWN,
                )
            )
    return messages


def _declared_zones_sentence(declared: list[str]) -> str:
    """Return the sentence naming the zones a composed page template declares."""
    if not declared:
        return "The page declares no zones."
    names = ", ".join(repr(name) for name in declared)
    return f"Declared zones: {names}."


def _zone_bound_contexts(
    bindings: "tuple[ZoneBinding, ...]",
) -> "Iterator[tuple[str, str]]":
    """Yield the label and bound zone name of every zone-bound `@context` of a page.

    The page-context registry keys on the file declaring the callable,
    which for a `page.py` is the very path the page scan walked, so the
    composed-page path looks the bindings up directly.
    """
    for binding in bindings:
        if binding.zones is None:
            continue
        label = (
            binding.name
            if binding.key is None
            else f'{binding.name} (key "{binding.key}")'
        )
        for zone_name in sorted(binding.zones):
            yield label, zone_name


@register(Tags.templates, NEXT)
def check_no_zone_in_component(*args, **kwargs) -> list[CheckMessage]:
    """Error when a component template declares a zone (`next.E065`)."""
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return []
    messages: list[CheckMessage] = []
    manager = get_components_manager()
    seen: set[Path] = set()
    for backend in manager.backends:
        for info in backend.iter_components():
            template_path = info.template_path
            if template_path is None:
                continue
            resolved = template_path.resolve()
            if resolved in seen or not resolved.exists():
                continue
            seen.add(resolved)
            messages.extend(_component_zone_errors(resolved))
    return messages


def _component_zone_errors(template_path: Path) -> list[CheckMessage]:
    """Return an error for each zone tag found in a component template file."""
    try:
        source = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    if "{% zone " not in source and "{%zone " not in source:
        return []
    return [
        Error(
            f"Component template {template_path} declares a {{% zone %}} tag. "
            "A zone belongs to a page, not a component. Move the zone into the "
            "page or layout template.",
            obj=str(template_path),
            id=E_ZONE_IN_COMPONENT,
        )
    ]


_OP_TOKEN = re.compile(r"\A[A-Za-z0-9_.-]+\Z")


@register(Tags.templates, NEXT)
def check_custom_patch_ops_well_formed(*args, **kwargs) -> list[CheckMessage]:
    """Error when a custom patch verb is malformed or shadows a built-in (`next.E066`).

    The runtime guard in `Patches.op()` rejects an unregistered verb on
    every call. This check turns the registry side of that contract into
    a startup error: a verb registered with a non-token name or one that
    silently shadows a built-in verb is caught at `manage.py check`
    rather than only when an op of that name reaches a client.
    """
    messages: list[CheckMessage] = []
    for name in sorted(patch_op_registry.custom_names()):
        if name in BUILTIN_OPS:
            messages.append(
                Error(
                    f'Custom patch op "{name}" shadows a built-in verb. The '
                    "built-in verb wins on the wire, so the custom handler "
                    "never runs. Register the op under a different name.",
                    id=E_UNREGISTERED_OP,
                )
            )
            continue
        if not _OP_TOKEN.match(name):
            messages.append(
                Error(
                    f'Custom patch op "{name}" is not a valid verb token. A '
                    "patch verb travels in the JSON envelope, so use letters, "
                    "digits, dots, hyphens, or underscores.",
                    id=E_UNREGISTERED_OP,
                )
            )
    return messages


@register(NEXT)
def check_form_backend_partial_aware(*args, **kwargs) -> list[CheckMessage]:
    """Warn when partial rendering is on but a form backend is not aware (`next.W068`).

    The base `FormActionBackend.shape_response` routes partial requests
    to the patch shaping path. A custom backend that overrides
    `shape_response` without that branch would silently drop the patch
    envelope and serve a full page to the runtime. The check stays silent
    on the default backend, which inherits the partial-aware method.
    """
    if not _partial_backends_active():
        return []
    messages: list[CheckMessage] = []
    seen: set[type] = set()
    for backend in form_action_manager.backends:
        backend_class = type(backend)
        if backend_class in seen:
            continue
        seen.add(backend_class)
        if backend_class.shape_response is FormActionBackend.shape_response:
            continue
        messages.append(
            DjangoWarning(
                f"Form action backend {backend_class.__name__!r} overrides "
                "shape_response, but PARTIAL_BACKENDS is configured. Route "
                "partial requests through next.partial.shape_partial in the "
                "override, or the runtime receives a full page instead of a "
                "patch envelope.",
                id=W_FORM_BACKEND_NOT_AWARE,
            )
        )
    return messages


@register(NEXT)
def check_partial_backends_is_a_list(*args, **kwargs) -> list[CheckMessage]:
    """Error when `PARTIAL_BACKENDS` is not a list (`next.E067`).

    The settings layer merges the key only when it holds a list, so a tuple
    or a bare dict is dropped and the default protocol backend loads in its
    place. This check owns the shape probe for the key, which the
    configuration checks leave out so the drop is reported once.
    """
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if not isinstance(raw, dict):
        return []
    configs = raw.get(_PARTIAL_BACKENDS_KEY)
    if configs is None or isinstance(configs, list):
        return []
    return [
        Error(
            f"NEXT_FRAMEWORK[{_PARTIAL_BACKENDS_KEY!r}] must be a list. The "
            "value is ignored, so the default protocol backend loads instead "
            "of the configured one.",
            obj=settings,
            id=E_BACKENDS_NOT_A_LIST,
        )
    ]


def _partial_backend_configs() -> list[object]:
    """Return PARTIAL_BACKENDS as a list, tolerating any malformed shape."""
    configs = getattr(next_framework_settings, _PARTIAL_BACKENDS_KEY, ())
    if isinstance(configs, list | tuple):
        return list(configs)
    return []


def _partial_backends_active() -> bool:
    """Return True when at least one partial protocol backend is configured."""
    return any(isinstance(config, dict) for config in _partial_backend_configs())


@register(NEXT)
def check_single_partial_backend(*args, **kwargs) -> list[CheckMessage]:
    """Warn when more than one partial protocol backend is configured (`next.W071`).

    Partial rendering uses a single protocol backend. Only the first valid
    PARTIAL_BACKENDS entry is instantiated, so a second entry is dead config
    that silently never runs.
    """
    valid = [
        config for config in _partial_backend_configs() if isinstance(config, dict)
    ]
    if len(valid) <= 1:
        return []
    return [
        DjangoWarning(
            "PARTIAL_BACKENDS has more than one backend entry, but partial "
            "rendering uses a single protocol backend. Only the first entry "
            "runs, the rest are ignored. Keep one PARTIAL_BACKENDS entry.",
            id=W_TOO_MANY_BACKENDS,
        )
    ]


_VERSION_OPTION: Final = "VERSION"
_MANIFEST_VERSION: Final = "manifest"
_STATICFILES_ALIAS: Final = "staticfiles"


@register(NEXT)
def check_partial_backend_names_a_path(*args, **kwargs) -> list[CheckMessage]:
    """Error when a PARTIAL_BACKENDS entry omits its BACKEND key (`next.E073`).

    Such an entry falls back to the default protocol backend, so the
    intended wire format would silently never load. The check names the
    entry that lacks a dotted path at startup instead.
    """
    messages: list[CheckMessage] = []
    for index, config in enumerate(_partial_backend_configs()):
        if not isinstance(config, dict) or "BACKEND" in config:
            continue
        messages.append(
            Error(
                f"PARTIAL_BACKENDS entry {index} has no BACKEND key. Every "
                "entry names its protocol backend by dotted path under "
                "BACKEND, so add it or drop the entry.",
                id=E_BACKEND_WITHOUT_PATH,
            )
        )
    return messages


@register(NEXT)
def check_manifest_version_has_manifest_storage(*args, **kwargs) -> list[CheckMessage]:
    """Warn when manifest versioning has no manifest storage (`next.W069`).

    The `VERSION: "manifest"` option asks the version stamp to track the
    staticfiles manifest, so a deploy of new assets bumps the version and
    the client reloads. That guard is silent unless the active staticfiles
    storage hashes its files into a manifest. The check pairs with the
    runtime fallback that resolves the sentinel to a stable default when no
    manifest storage is configured, surfacing the dead guard at startup.
    """
    if not _manifest_version_requested():
        return []
    if _staticfiles_storage_is_manifest():
        return []
    return [
        DjangoWarning(
            'A partial backend sets VERSION: "manifest", but the staticfiles '
            "storage does not hash files into a manifest. The asset-version "
            "guard stays silent, so a deploy of new assets cannot ask clients "
            "to reload. Use a ManifestStaticFilesStorage, or set an explicit "
            "VERSION string to pin the version yourself.",
            id=W_MANIFEST_VERSION_NO_STORAGE,
        )
    ]


def _manifest_version_requested() -> bool:
    """Return True when a partial backend resolves VERSION to the sentinel."""
    for config in _partial_backend_configs():
        if not isinstance(config, dict):
            continue
        options = config.get("OPTIONS")
        version = options.get(_VERSION_OPTION) if isinstance(options, dict) else None
        if version is None or version == _MANIFEST_VERSION:
            return True
    return False


def _staticfiles_storage_is_manifest() -> bool:
    """Return True when the configured staticfiles storage hashes its files.

    The storage class is read from its dotted path rather than the resolved
    `staticfiles_storage` proxy, so the check stays side-effect-free and
    never fails on a project that has not set STATIC_ROOT.
    """
    backend_path = _staticfiles_storage_path()
    if backend_path is None:
        return False
    try:
        storage_class = import_class_cached(backend_path)
    except ImportError:
        return False
    return isinstance(storage_class, type) and issubclass(
        storage_class, ManifestFilesMixin
    )


def _staticfiles_storage_path() -> str | None:
    """Return the dotted path of the configured staticfiles storage backend."""
    storages = getattr(settings, "STORAGES", None)
    if isinstance(storages, dict):
        entry = storages.get(_STATICFILES_ALIAS)
        if isinstance(entry, dict):
            backend = entry.get("BACKEND")
            return backend if isinstance(backend, str) else None
    return None


__all__ = [
    "E_BACKEND_WITHOUT_PATH",
    "E_COMPOSED_TEMPLATE_SYNTAX",
    "E_CONTEXT_ZONE_UNKNOWN",
    "E_DUPLICATE_ZONE",
    "E_LAZY_WITHOUT_PLACEHOLDER",
    "E_NON_ASCII_ZONE",
    "E_UNREGISTERED_OP",
    "E_ZONE_IN_COMPONENT",
    "E_ZONE_IN_FOR",
    "E_ZONE_IN_IF",
    "W_FORM_BACKEND_NOT_AWARE",
    "W_FORM_IN_FOR_NO_KEY",
    "W_MANIFEST_VERSION_NO_STORAGE",
    "W_TOO_MANY_BACKENDS",
    "W_WITH_OVER_ZONE",
    "check_composed_templates_compile",
    "check_context_zone_names_exist",
    "check_custom_patch_ops_well_formed",
    "check_duplicate_zone_names",
    "check_form_backend_partial_aware",
    "check_lazy_zone_has_placeholder",
    "check_manifest_version_has_manifest_storage",
    "check_no_zone_in_component",
    "check_partial_backend_names_a_path",
    "check_repeated_form_has_key",
    "check_single_partial_backend",
    "check_with_directly_over_zone",
    "check_zone_name_is_slug",
    "check_zone_not_in_if",
    "check_zone_not_in_loop",
    "reset_composed_pages_memo",
]
