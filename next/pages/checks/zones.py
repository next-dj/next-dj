"""System check for a `@context` reading a key another callable binds to a zone.

The id is `next.W077`, raised for the reader, which is what silently receives `None`.
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, NamedTuple

from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning, register
from django.http import HttpRequest

from next.checks import NEXT
from next.deps import RESERVED_KEYS, ResolutionContext, resolver
from next.deps.cache import DependencyCache
from next.ports import router_access_slot

from .contexts import loaded_page_contexts


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.pages.registry import ZoneBinding
    from next.urls import URLPatternParser


class _BoundProvider(NamedTuple):
    """The callable name and bound zones of a keyed zone-bound `@context`."""

    name: str
    zones: frozenset[str]


@register(Tags.templates, NEXT)
def check_context_reads_foreign_zone(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a `@context` reads a key bound to a zone it misses (`next.W077`).

    A zone request that does not name the bound zone skips the provider,
    while the reader still runs and receives `None` for the parameter.
    """
    init_errors, pages = loaded_page_contexts()
    warnings = list(init_errors)
    for entry in pages:
        warnings.extend(
            _foreign_zone_reads(entry.page_path, entry.url_path, entry.bindings)
        )
    return warnings


def _foreign_zone_reads(
    page_path: Path, url_path: str, bindings: tuple[ZoneBinding, ...]
) -> list[CheckMessage]:
    """Return a warning for each parameter reading a key bound to a foreign zone."""
    providers = _zone_bound_providers(bindings)
    if not providers:
        return []
    context = _zone_request_context(url_path)
    warnings: list[CheckMessage] = []
    for binding in bindings:
        for param in _context_parameters(binding.func):
            provider = providers.get(param.name)
            if provider is None or param.name == binding.key:
                continue
            if resolver.provides(binding.func, param, context):
                continue
            if binding.zones is not None and not binding.zones.isdisjoint(
                provider.zones
            ):
                continue
            warnings.append(
                _foreign_zone_warning(page_path, binding.name, param.name, provider)
            )
    return warnings


def _zone_request_context(url_path: str) -> ResolutionContext:
    """Build the resolution context of a zone request that carries no context data.

    The `context_data` is empty on purpose, a parameter some provider still
    fills is not waiting on the zone-bound `@context`.
    """
    return ResolutionContext(
        request=HttpRequest(),
        form=None,
        url_kwargs=dict.fromkeys(_url_parameter_names(url_path), ""),
        context_data={},
        cache=DependencyCache(),
    )


def _zone_bound_providers(
    bindings: tuple[ZoneBinding, ...],
) -> dict[str, _BoundProvider]:
    """Return the callable name and zones of every keyed zone-bound `@context`."""
    providers: dict[str, _BoundProvider] = {}
    for binding in bindings:
        zones = binding.zones
        if binding.key is None or zones is None:
            continue
        providers[binding.key] = _BoundProvider(name=binding.name, zones=zones)
    return providers


def _context_parameters(func: Callable[..., Any]) -> list[inspect.Parameter]:
    """Return the parameters of a context callable that the context alone fills.

    A parameter carrying a default is left out, because the resolver falls
    back to that default and a `Depends` or `Context` marker travels as one.
    """
    try:
        parameters = inspect.signature(func).parameters
    except (TypeError, ValueError):
        return []
    return [
        param
        for param in parameters.values()
        if param.default is inspect.Parameter.empty
        and not resolver.skips(param)
        and param.name not in RESERVED_KEYS
    ]


def _url_parameter_names(url_path: str) -> list[str]:
    """Return the URL kwarg names the route captures, as the router parses them.

    A route the parser refuses captures nothing, because `next.E011` reports it.
    """
    parser = _url_parser()
    try:
        _pattern, parameters = parser.parse_url_pattern(url_path)
    except ValueError:
        return []
    return list(parameters)


def _url_parser() -> URLPatternParser:
    """Return the parser the file router routes bracket segments through."""
    return router_access_slot.get().url_parser()


def _foreign_zone_warning(
    page_path: Path, reader: str, param_name: str, provider: _BoundProvider
) -> CheckMessage:
    """Return the `next.W077` warning naming the reader and the bound provider."""
    provider_name = provider.name
    joined = ", ".join(repr(zone) for zone in sorted(provider.zones))
    return DjangoWarning(
        f"Context callable {reader} in {page_path} takes the parameter "
        f"'{param_name}', which {provider_name} provides under that key while "
        f"bound to zone {joined}. A zone request that names another zone skips "
        f"{provider_name}, so {reader} runs with {param_name}=None. Bind "
        f"{reader} to the same zone with zone=, or have it handle the None it "
        "receives outside that zone.",
        obj=str(page_path),
        id="next.W077",
    )


__all__ = ["check_context_reads_foreign_zone"]
