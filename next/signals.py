"""Aggregate every signal the framework emits and the `receiver` that subscribes to one.

Import from here when one module subscribes to several subsystems at once.
"""

from collections.abc import Callable
from functools import partial, update_wrapper
from inspect import iscoroutinefunction
from typing import Final

from asgiref.sync import async_to_sync
from django.db import transaction
from django.dispatch import Signal, receiver as django_receiver

from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)
from next.conf.signals import settings_reloaded
from next.consent.signals import consent_backend_loaded
from next.deps.signals import provider_registered
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_access_denied,
    form_backend_loaded,
    form_validation_failed,
    wizard_backend_loaded,
    wizard_completed,
    wizard_step_submitted,
)
from next.pages.signals import (
    context_registered,
    metadata_registered,
    page_rendered,
    template_loaded,
)
from next.partial.signals import (
    field_validated,
    partial_backend_loaded,
    patch_op_registered,
    sse_stream_closed,
    sse_stream_opened,
    zone_registered,
    zone_rendered,
)
from next.scripts.signals import scripts_registered
from next.seo.signals import sitemap_backend_loaded
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    collector_finalized,
    html_injected,
    static_backend_loaded,
)
from next.urls.signals import route_registered, router_backend_loaded, router_reloaded


_WEAK_ON_COMMIT: Final = (
    "receiver(on_commit=True) keeps its handler alive for the life of the process, "
    "so it cannot connect weakly. Drop weak=True."
)


def _identity(func: Callable[..., object]) -> int | tuple[int, int]:
    """Return the id Django keys *func* by, so `disconnect(func)` finds its handler.

    A plain connection of it to the same sender shares the key, so the later is ignored.
    """
    if hasattr(func, "__func__") and hasattr(func, "__self__"):
        return (id(func.__self__), id(func.__func__))
    return id(func)


def _deferred(func: Callable[..., object]) -> Callable[..., None]:
    """Return a receiver that hands *func* to `transaction.on_commit` with its kwargs.

    A model signal names its database in `using`, so the call waits for that commit.
    """
    target = async_to_sync(func) if iscoroutinefunction(func) else func

    def deferred(**kwargs: object) -> None:
        using = kwargs.get("using")
        alias = using if isinstance(using, str) else None
        transaction.on_commit(partial(target, **kwargs), using=alias)

    return update_wrapper(deferred, func, updated=())


def receiver[F: Callable[..., object]](
    signal: Signal | list[Signal] | tuple[Signal, ...],
    *,
    on_commit: bool = False,
    **kwargs,
) -> Callable[[F], F]:
    """Connect the decorated function the way Django's `receiver` does, returning it.

    With `on_commit` it runs only once the transaction commits, and `send` gets `None`.
    """
    if not on_commit:
        return django_receiver(signal, **kwargs)
    if kwargs.pop("weak", False):
        raise ValueError(_WEAK_ON_COMMIT)

    def decorator(func: F) -> F:
        uid = kwargs.get("dispatch_uid") or _identity(func)
        options = {**kwargs, "dispatch_uid": uid}
        django_receiver(signal, weak=False, **options)(_deferred(func))
        return func

    return decorator


__all__ = [
    "action_dispatched",
    "action_registered",
    "asset_registered",
    "collector_finalized",
    "component_backend_loaded",
    "component_registered",
    "component_rendered",
    "components_registered",
    "consent_backend_loaded",
    "context_registered",
    "field_validated",
    "form_access_denied",
    "form_backend_loaded",
    "form_validation_failed",
    "html_injected",
    "metadata_registered",
    "page_rendered",
    "partial_backend_loaded",
    "patch_op_registered",
    "provider_registered",
    "receiver",
    "route_registered",
    "router_backend_loaded",
    "router_reloaded",
    "scripts_registered",
    "settings_reloaded",
    "sitemap_backend_loaded",
    "sse_stream_closed",
    "sse_stream_opened",
    "static_backend_loaded",
    "template_loaded",
    "watch_specs_ready",
    "wizard_backend_loaded",
    "wizard_completed",
    "wizard_step_submitted",
    "zone_registered",
    "zone_rendered",
]
