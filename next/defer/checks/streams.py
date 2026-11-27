"""The checks of what a page stream needs, its runtime bundle, flushes and CSP."""

from collections.abc import Iterator, Mapping
from typing import Final, cast

import django
from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning

from next.checks import NEXT, register
from next.compression.manager import FLUSHING_GZIP, GZIP_MIDDLEWARE, encodes_documents
from next.conf import next_framework_settings
from next.conf.compression import compression_settings
from next.conf.defer import defer_option
from next.pages.checks.composed import iter_composed_pages
from next.partial.checks.nodes import zone_nodes
from next.partial.more import MoreNode
from next.partial.zone import LOADING
from next.static.nonce import CSP_MIDDLEWARE
from next.static.runtime import ScriptInjectionPolicy
from next.utils import middleware_listed

from .calls import mints_tokens


_DJANGO_CSP: Final = CSP_MIDDLEWARE[0]
_NONCE_SOURCE: Final = "<CSP_NONCE_SENTINEL>"
"""The value of `django.utils.csp.CSP.NONCE`, the placeholder the middleware fills."""

_UNSAFE_INLINE: Final = "'unsafe-inline'"
_OVERRIDING: Final = ("'nonce-", "'sha256-", "'sha384-", "'sha512-", "'strict-dynamic'")
_SCRIPT_DIRECTIVES: Final = ("script-src-elem", "script-src", "default-src")


def _runtime_disabled() -> bool:
    """Whether `NEXT_JS_OPTIONS['policy']` names `disabled`, an unknown one not."""
    options = next_framework_settings.NEXT_JS_OPTIONS
    if not isinstance(options, dict):
        return False
    try:
        policy = ScriptInjectionPolicy(
            options.get("policy", ScriptInjectionPolicy.AUTO)
        )
    except ValueError:
        return False
    return policy is ScriptInjectionPolicy.DISABLED


def _template_needs() -> Iterator[str]:
    """Yield what a composed page template declares that a bundle serves."""
    for page_path, template in iter_composed_pages():
        for zone in zone_nodes(template):
            if zone.options.reload == LOADING:
                yield f'the zone "{zone.name}" of {page_path} reloads to its loading UI'
        mores = cast("list[MoreNode]", template.nodelist.get_nodes_by_type(MoreNode))
        for node in mores:
            if node.options.windowed:
                yield f"the {{% more %}} of {page_path} sets prune= or url="


def _first_need() -> str | None:
    """Return the first need a bundle serves, the templates read before components."""
    found = next(_template_needs(), None)
    if found is not None or not mints_tokens():
        return found
    return "a component loads on a later request or declares props"


@register(Tags.templates, NEXT)
def check_bundles_have_a_runtime(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn when the `disabled` script policy drops a bundle a page needs (`next.W181`).

    Each bundle registers with the runtime, so without it no bundle tag renders.
    """
    if not _runtime_disabled():
        return []
    need = _first_need()
    if need is None:
        return []
    return [
        DjangoWarning(
            f"NEXT_JS_OPTIONS['policy'] is 'disabled' while {need}. The browser code "
            "of that markup is a bundle that registers with the runtime, so no page "
            "loads it and the markup stays inert.",
            hint=(
                "Set the policy to 'auto', or to 'manual' with a tag of your own that "
                "emits the runtime before {% collect_scripts %}."
            ),
            obj=settings,
            id="next.W181",
        )
    ]


def _streams() -> bool:
    """Whether the delivery policy may stream, as nothing sets `STREAM` to `False`."""
    return defer_option("DELIVERY", "OPTIONS", "STREAM") is not False


@register(NEXT)
def check_buffering_gzip(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that Django's `GZipMiddleware` holds every page stream back (`next.W153`).

    Below Django 6.1 it compresses a stream without flushing, so the policy inlines it.
    """
    if (
        django.VERSION[:2] >= FLUSHING_GZIP
        or not _streams()
        or encodes_documents(compression_settings())
        or not middleware_listed(settings.MIDDLEWARE, GZIP_MIDDLEWARE)
    ):
        return []
    return [
        DjangoWarning(
            f"{GZIP_MIDDLEWARE} is listed while next.dj encodes no page stream, and "
            "below Django 6.1 it compresses a stream without flushing, so every page "
            "a gzip client asks for is assembled inline rather than streamed.",
            hint=(
                "Replace it with next.middleware.CompressionMiddleware and keep "
                "NEXT_FRAMEWORK['COMPRESSION']['ENABLED'] on, or upgrade to Django 6.1."
            ),
            obj=settings,
            id="next.W153",
        )
    ]


def _script_sources(policy: object) -> tuple[str, ...] | None:
    """Return the sources a policy lets script elements run from, `None` for any."""
    if not isinstance(policy, Mapping):
        return None
    for directive in _SCRIPT_DIRECTIVES:
        value = policy.get(directive)
        if isinstance(value, str):
            return tuple(value.split())
        if isinstance(value, list | tuple | set | frozenset):
            return tuple(str(source) for source in value)
    return None


def _inline_allowed(sources: tuple[str, ...]) -> bool:
    """Whether `sources` run an inline script with no nonce, as `'unsafe-inline'` does.

    A nonce, a hash or `'strict-dynamic'` beside it makes browsers ignore it.
    """
    return _UNSAFE_INLINE in sources and not any(
        source == _NONCE_SOURCE or source.startswith(_OVERRIDING) for source in sources
    )


def _refused_scripts() -> str | None:
    """Return why the CSP refuses the inline scripts of a page stream, `None` if not.

    Only the policy of Django's own middleware is readable, django-csp's is not read.
    """
    middleware = settings.MIDDLEWARE
    listed = [path for path in CSP_MIDDLEWARE if middleware_listed(middleware, path)]
    if not listed:
        return None
    sources = None
    if _DJANGO_CSP in listed:
        sources = _script_sources(getattr(settings, "SECURE_CSP", None))
        if sources is None or _inline_allowed(sources):
            return None
    if not next_framework_settings.CSP_NONCE:
        return "NEXT_FRAMEWORK['CSP_NONCE'] is off"
    if sources is not None and _NONCE_SOURCE not in sources:
        return "the script sources of SECURE_CSP hold no CSP.NONCE"
    return None


@register(NEXT)
def check_stream_nonce(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn that the CSP refuses the inline scripts a page stream writes (`next.W160`).

    Each chunk is an inline script, so a policy that refuses it keeps the loading UI.
    """
    if not _streams():
        return []
    reason = _refused_scripts()
    if reason is None:
        return []
    return [
        DjangoWarning(
            "A streamed page writes each deferred boundary as an inline script, and "
            f"a CSP middleware is listed while {reason}, so the browser refuses "
            "those scripts and every boundary keeps its loading UI.",
            hint=(
                "Keep NEXT_FRAMEWORK['CSP_NONCE'] on and put the nonce in the "
                "script-src of the policy, or set "
                "DEFER['DELIVERY']['OPTIONS']['STREAM'] to False."
            ),
            obj=settings,
            id="next.W160",
        )
    ]


__all__ = ["check_buffering_gzip", "check_bundles_have_a_runtime", "check_stream_nonce"]
