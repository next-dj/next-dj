"""System checks for the static subsystem."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, Final

from django.conf import settings
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.finders import AppDirectoriesFinder
from django.contrib.staticfiles.storage import staticfiles_storage
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import import_backend_class
from next.components.sources import iter_serialized_component_context_keys
from next.conf import import_class_cached, next_framework_settings
from next.pages.checks.responses import private_pages_warning
from next.pages.scan import iter_serialized_page_context_keys

from .assets import default_kinds
from .backends import StaticBackend, StaticFilesBackend
from .finders import NextAppDirectoriesFinder
from .nonce import nonce_active
from .runtime import (
    CHUNK_STATIC_PATHS,
    DEV_CHUNK_STATIC_PATH,
    INIT_FIELDS,
    NEXT_JS_STATIC_PATH,
    RESERVED_PAYLOAD_KEYS,
    TAG_FIELDS,
    TEMPLATE_ERRORS,
    NextScriptBuilder,
    ScriptInjectionPolicy,
    dry_run_template,
)
from .serializers import JsContextSerializer


if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path


def _validate_tag_template(
    tag_name: str, value: object, backend_index: int
) -> CheckMessage | None:
    if not isinstance(value, str):
        return None
    if "{url}" not in value:
        return DjangoWarning(
            (
                f"OPTIONS[{tag_name!r}] in STATIC_BACKENDS[{backend_index}] "
                "does not contain the '{url}' placeholder. Rendered tags will "
                "not include the asset URL."
            ),
            obj=settings,
            id="next.W031",
        )
    return None


def _check_single_backend(
    config: object, index: int, seen: set[str]
) -> Iterable[CheckMessage]:
    messages: list[CheckMessage] = []
    if not isinstance(config, dict):
        messages.append(
            Error(
                f"STATIC_BACKENDS[{index}] must be a dict, got "
                f"{type(config).__name__!r}.",
                obj=settings,
                id="next.E037",
            )
        )
        return messages
    backend_path = config.get("BACKEND", "next.static.StaticFilesBackend")
    if not isinstance(backend_path, str):
        messages.append(
            Error(
                f"STATIC_BACKENDS[{index}]['BACKEND'] must be a dotted "
                f"string, got {type(backend_path).__name__!r}.",
                obj=settings,
                id="next.E092",
            )
        )
        return messages
    if backend_path in seen:
        messages.append(
            Error(
                f"STATIC_BACKENDS has duplicate BACKEND entry {backend_path!r}.",
                obj=settings,
                id="next.E038",
            )
        )
        return messages
    seen.add(backend_path)
    try:
        backend_class = import_class_cached(backend_path)
    except ImportError as e:
        messages.append(
            Error(
                f"Cannot import static backend {backend_path!r}: {e}",
                obj=settings,
                id="next.E036",
            )
        )
        return messages
    if not isinstance(backend_class, type) or not issubclass(
        backend_class, StaticBackend
    ):
        messages.append(
            Error(
                f"Static backend {backend_path!r} is not a StaticBackend subclass.",
                obj=settings,
                id="next.E093",
            )
        )
        return messages
    options: Any = config.get("OPTIONS") or {}
    if isinstance(options, dict):
        for tag_name in ("css_tag", "js_tag", "module_tag"):
            if tag_name not in options:
                continue
            message = _validate_tag_template(tag_name, options[tag_name], index)
            if message is not None:
                messages.append(message)
    return messages


@register(NEXT)
def check_static_backends(**kwargs) -> list[CheckMessage]:
    """Validate the structure of `NEXT_FRAMEWORK['STATIC_BACKENDS']`."""
    messages: list[CheckMessage] = []
    configs = next_framework_settings.STATIC_BACKENDS
    if not isinstance(configs, list) or len(configs) == 0:
        messages.append(
            DjangoWarning(
                "NEXT_FRAMEWORK['STATIC_BACKENDS'] is empty. The "
                "framework falls back to next.static.StaticFilesBackend.",
                obj=settings,
                id="next.W030",
            )
        )
        return messages

    seen: set[str] = set()
    for index, config in enumerate(configs):
        messages.extend(_check_single_backend(config, index, seen))
    return messages


@register(NEXT)
def check_asset_kinds_are_loadable(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a registered kind the partial runtime cannot insert."""
    return [
        DjangoWarning(
            f"Asset kind {kind!r} is registered with renderer "
            f"{default_kinds.renderer(kind)!r}, which carries no client "
            "insertion verb. Assets of this kind reach the browser only on a "
            "full page render, never through a patch envelope. Register the "
            "kind with render_link_tag, render_script_tag, or render_module_tag.",
            obj=settings,
            id="next.W074",
        )
        for kind in default_kinds.kinds()
        if default_kinds.load(kind) is None
    ]


_DEFAULT_BACKEND: Final = "next.static.StaticFilesBackend"
_RENDER_KEYWORDS: Final = frozenset({"request", "nonce"})
_NAMED: Final = frozenset(
    {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
)


def _rendering_backend() -> tuple[str, type[StaticBackend]]:
    """Return the backend every render takes its tags from, the first one that loads."""
    configs = next_framework_settings.STATIC_BACKENDS
    for config in configs if isinstance(configs, list) else ():
        if not isinstance(config, dict):
            continue
        path = config.get("BACKEND", _DEFAULT_BACKEND)
        if not isinstance(path, str):
            continue
        try:
            backend = import_class_cached(path)
        except ImportError:
            continue
        if isinstance(backend, type) and issubclass(backend, StaticBackend):
            return path, backend
    return _DEFAULT_BACKEND, StaticFilesBackend


def _takes_render_keywords(method: Callable[..., object]) -> bool:
    """Whether a renderer accepts the `request` and `nonce` every render passes it."""
    try:
        parameters = inspect.signature(method).parameters.values()
    except (TypeError, ValueError):
        return True
    if any(param.kind is inspect.Parameter.VAR_KEYWORD for param in parameters):
        return True
    named = {param.name for param in parameters if param.kind in _NAMED}
    return named >= _RENDER_KEYWORDS


def _renderer_problem(backend: type[StaticBackend], name: str) -> str | None:
    method = getattr(backend, name, None)
    if not callable(method):
        return f"has no {name} method"
    if not _takes_render_keywords(method):
        return f"defines {name} without the request and nonce keywords"
    return None


@register(NEXT)
def check_asset_renderers(*args, **kwargs) -> list[CheckMessage]:
    """Report a kind whose renderer the rendering backend cannot call (`next.E147`)."""
    path, backend = _rendering_backend()
    return [
        Error(
            f"Asset kind {kind!r} renders through {path}, which {problem}, so every "
            "page holding such an asset fails to render.",
            hint=(
                "Define the renderer as (self, url, *, request=None, nonce=None) and "
                "give the tag it returns a nonce attribute whenever nonce is set."
            ),
            obj=settings,
            id="next.E147",
        )
        for kind in default_kinds.kinds()
        if (problem := _renderer_problem(backend, default_kinds.renderer(kind)))
    ]


def _loses_inline_bodies(kind: str) -> bool:
    """Return True when a kind's URL form has an insertion verb but its bodies do not.

    A kind without an `inline_tag` is left to `next.W074`, since its inline
    bodies render verbatim and name no element either render can build.
    """
    if default_kinds.inline_tag(kind) is None:
        return False
    return (
        default_kinds.load(kind) is not None
        and default_kinds.load(kind, inline=True) is None
    )


@register(NEXT)
def check_inline_asset_bodies_are_loadable(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a kind whose inline bodies the partial runtime cannot insert."""
    return [
        DjangoWarning(
            f"Asset kind {kind!r} is registered with renderer "
            f"{default_kinds.renderer(kind)!r} and inline_tag "
            f"{default_kinds.inline_tag(kind)!r}, which name different elements. "
            "URL-form assets of this kind travel in a patch envelope, but their "
            "inline bodies carry no client insertion verb and reach the browser "
            "only on a full page render. Pair render_link_tag with "
            "inline_tag='style' or render_script_tag with inline_tag='script'.",
            obj=settings,
            id="next.W076",
        )
        for kind in default_kinds.kinds()
        if _loses_inline_bodies(kind)
    ]


def _reserved_key_warning(origin: str, source_path: Path, key: str) -> CheckMessage:
    """Return the `next.W075` warning for one reserved-key collision."""
    return DjangoWarning(
        f"{origin} context key {key!r} in {source_path} is reserved for the "
        f"next.min.js init payload. The framework owns {key} on every render, so "
        "the automatically injected payload drops the registered value before it "
        "reaches window.Next.context and no context patch can update it. "
        "Rename the key.",
        obj=str(source_path),
        id="next.W075",
    )


@register(NEXT)
def check_reserved_js_context_keys(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a page or component context key the init payload reserves.

    Pages and components feed the same init payload, so both registries are walked.
    """
    sources = (
        ("Page", iter_serialized_page_context_keys()),
        ("Component", iter_serialized_component_context_keys()),
    )
    return [
        _reserved_key_warning(origin, source_path, key)
        for origin, entries in sources
        for source_path, key in entries
        if key in RESERVED_PAYLOAD_KEYS
    ]


def _serializer_warning(message: str, code: str) -> CheckMessage:
    """Return a JS_CONTEXT_SERIALIZER warning tied to settings as the object."""
    return DjangoWarning(message, obj=settings, id=code)


@register(NEXT)
def check_js_context_serializer(*args, **kwargs) -> list[CheckMessage]:
    """Validate that `JS_CONTEXT_SERIALIZER` resolves to a protocol implementation."""
    message = _js_context_serializer_message()
    return [message] if message is not None else []


def _js_context_serializer_message() -> CheckMessage | None:
    """Return the single configuration warning for `JS_CONTEXT_SERIALIZER`.

    An unset option or a non-dict `NEXT_FRAMEWORK` is healthy and returns
    None, so the registered check stays a thin list wrapper.
    """
    raw_framework = getattr(settings, "NEXT_FRAMEWORK", {}) or {}
    if not isinstance(raw_framework, dict):
        return None
    path = raw_framework.get("JS_CONTEXT_SERIALIZER")
    if path is None or path == "":
        return None
    if not isinstance(path, str):
        return _serializer_warning(
            f"NEXT_FRAMEWORK['JS_CONTEXT_SERIALIZER'] must be a dotted path "
            f"string, got {type(path).__name__!r}.",
            "next.W042",
        )
    return _js_context_serializer_instance_message(path)


def _js_context_serializer_instance_message(path: str) -> CheckMessage | None:
    """Import and instantiate the configured serializer, reporting any failure."""
    try:
        cls: Any = import_class_cached(path)
    except ImportError as e:
        return _serializer_warning(
            f"Cannot import JS_CONTEXT_SERIALIZER {path!r}: {e}", "next.W079"
        )
    if not isinstance(cls, type):
        return _serializer_warning(
            f"JS_CONTEXT_SERIALIZER {path!r} is not a class.", "next.W080"
        )
    try:
        instance = cls()
    except (TypeError, ImportError) as e:
        return _serializer_warning(
            f"JS_CONTEXT_SERIALIZER {path!r} cannot be instantiated: {e}", "next.W081"
        )
    if not isinstance(instance, JsContextSerializer):
        return _serializer_warning(
            f"JS_CONTEXT_SERIALIZER {path!r} does not implement the "
            "JsContextSerializer protocol (needs a `dumps(value) -> str` method).",
            "next.W082",
        )
    return None


def _publishes_framework_package(path: object) -> bool:
    """Whether a configured finder walks app static dirs without skipping our own."""
    if not isinstance(path, str):
        return False
    try:
        finder_class = import_backend_class(path)
    except ImportError:
        return False
    return (
        isinstance(finder_class, type)
        and issubclass(finder_class, AppDirectoriesFinder)
        and not issubclass(finder_class, NextAppDirectoriesFinder)
    )


@register(NEXT)
def check_app_directories_finder(*args, **kwargs) -> list[CheckMessage]:
    """Refuse an app-directories finder that publishes the framework package.

    An error rather than a warning, because a warning leaves `collectstatic` free to
    copy the framework's own sources into a directory the web server hands out.
    """
    return [
        Error(
            f"STATICFILES_FINDERS entry {path!r} extends Django's "
            "AppDirectoriesFinder without extending "
            "next.static.NextAppDirectoriesFinder, so it treats next/static as "
            "an app static directory. That directory is the next.static Python "
            "package, so collectstatic would publish the framework's own modules "
            "and their bytecode cache into STATIC_ROOT. Subclass "
            "next.static.NextAppDirectoriesFinder instead.",
            obj=settings,
            id="next.E083",
        )
        for path in getattr(settings, "STATICFILES_FINDERS", [])
        if _publishes_framework_package(path)
    ]


_RUNTIME_TEMPLATES: Final = ("preload_template", "script_tag_template", "init_template")
_BACKEND_TEMPLATES: Final = ("css_tag", "js_tag", "module_tag")


def _templates() -> Iterable[tuple[str, object]]:
    """Yield every configured tag template with the setting that holds it."""
    options = next_framework_settings.NEXT_JS_OPTIONS
    if isinstance(options, dict):
        for key in _RUNTIME_TEMPLATES:
            yield f"NEXT_JS_OPTIONS[{key!r}]", options.get(key)
    configs = next_framework_settings.STATIC_BACKENDS
    for index, config in enumerate(configs if isinstance(configs, list) else ()):
        opts = config.get("OPTIONS") if isinstance(config, dict) else None
        if isinstance(opts, dict):
            for key in _BACKEND_TEMPLATES:
                yield f"STATIC_BACKENDS[{index}]['OPTIONS'][{key!r}]", opts.get(key)


@register(NEXT)
def check_nonce_templates(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a custom tag template with no `{nonce_attr}` (`next.W117`)."""
    if not nonce_active():
        return []
    return [
        DjangoWarning(
            f"{where} holds no {{nonce_attr}} placeholder while a CSP nonce is "
            "active, so the tag it renders is refused by the policy. Add "
            "{nonce_attr} where the attributes go.",
            obj=settings,
            id="next.W117",
        )
        for where, template in _templates()
        if isinstance(template, str) and template and "{nonce_attr}" not in template
    ]


@register(NEXT)
def check_nonce_on_shared_pages(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a CSP nonce takes every page a CDN may hold private (`next.W120`)."""
    if not nonce_active():
        return []
    return private_pages_warning(
        "a CSP nonce is active and a nonce belongs to one visitor. Set "
        "NEXT_FRAMEWORK['CSP_NONCE'] to False and allow the scripts by hash or "
        "source, or drop the shared cache.",
        "next.W120",
    )


@register(NEXT)
def check_script_policy(*args, **kwargs) -> list[CheckMessage]:
    """Require `NEXT_JS_OPTIONS["policy"]` to name an injection policy (`next.E130`).

    The builder reads it the way a render does, so the two cannot disagree.
    """
    options = next_framework_settings.NEXT_JS_OPTIONS
    if not isinstance(options, dict):
        return []
    policy = options.get("policy", ScriptInjectionPolicy.AUTO)
    try:
        NextScriptBuilder.from_options("", {"policy": policy})
    except ValueError:
        allowed = ", ".join(repr(member.value) for member in ScriptInjectionPolicy)
        return [
            Error(
                f"NEXT_JS_OPTIONS['policy'] is {policy!r}, which names no injection "
                f"policy, so pages inject the runtime as under 'auto'. Write one of "
                f"{allowed}.",
                obj=settings,
                id="next.E130",
            )
        ]
    return []


def _template_fields(where: str) -> tuple[str, ...]:
    return INIT_FIELDS if "init_template" in where else TAG_FIELDS


@register(NEXT)
def check_tag_templates_format(*args, **kwargs) -> list[CheckMessage]:
    """Require every custom tag template to format with its fields (`next.E139`)."""
    errors: list[CheckMessage] = []
    for where, template in _templates():
        if not isinstance(template, str) or not template:
            continue
        fields = _template_fields(where)
        try:
            dry_run_template(template, fields)
        except TEMPLATE_ERRORS as exc:
            listed = ", ".join(f"{{{name}}}" for name in fields)
            errors.append(
                Error(
                    f"{where} is {template!r}, which does not format with {listed} "
                    f"({type(exc).__name__}: {exc}), so the default tag renders "
                    "instead.",
                    hint="Double every literal brace as {{ or }} and name no other "
                    "field.",
                    obj=settings,
                    id="next.E139",
                )
            )
    return errors


_DEPLOYED_BUNDLES: Final = (
    NEXT_JS_STATIC_PATH,
    *(path for path in CHUNK_STATIC_PATHS.values() if path != DEV_CHUNK_STATIC_PATH),
)
"""The runtime bundles a production page may load, the dev chunk left out."""


def _bundle_problem(path: str) -> str | None:
    """Say why the storage cannot serve a runtime bundle, `None` when it can."""
    if not finders.find(path):
        return "no static files finder answers it, so the client runtime is unbuilt"
    try:
        staticfiles_storage.url(path)
    except ValueError:
        return "the static files storage holds no entry for it"
    return None


@register(NEXT, deploy=True)
def check_runtime_bundles_deployed(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a runtime bundle the deployed storage cannot serve (`next.W090`)."""
    return [
        DjangoWarning(
            f"{path}: {problem}. Pages render without the runtime, or without the "
            "feature the chunk carries.",
            hint=(
                "Build the client runtime (npm run build:next in a source checkout) "
                "and run manage.py collectstatic."
            ),
            obj=settings,
            id="next.W090",
        )
        for path in _DEPLOYED_BUNDLES
        if (problem := _bundle_problem(path)) is not None
    ]
