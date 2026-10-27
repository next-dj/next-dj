from pathlib import Path

from django.conf import settings
from django.dispatch import receiver

from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)
from next.conf.signals import settings_reloaded
from next.deps.signals import provider_registered
from next.forms.signals import (
    action_dispatched,
    action_registered,
    form_backend_loaded,
    form_validation_failed,
    wizard_backend_loaded,
)
from next.pages.signals import context_registered, page_rendered, template_loaded
from next.partial.signals import partial_backend_loaded
from next.realtime import Upstream
from next.realtime.frames import UpstreamError
from next.realtime.signals import (
    message_published,
    stream_subscribed,
    stream_unsubscribed,
    upstream_received,
    upstream_rejected,
)
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    collector_finalized,
    html_injected,
    static_backend_loaded,
)
from next.urls.signals import route_registered, router_backend_loaded, router_reloaded

from .exporter import (
    MESSAGES_PUBLISHED,
    PRESENCE_SNAPSHOTS,
    STREAM_DURATION,
    STREAM_UNSUBSCRIBES,
    STREAMS_OPEN,
    UPSTREAM_EVENTS,
    UPSTREAM_REJECTED,
    topic_family,
)
from .metrics import incr


BACKEND_LOADED_SIGNALS = (
    component_backend_loaded,
    form_backend_loaded,
    partial_backend_loaded,
    router_backend_loaded,
    static_backend_loaded,
    wizard_backend_loaded,
)


def page_key(file_path: object) -> str:
    """Return the page path relative to the project root.

    The signal carries an absolute path that would leak the home directory.
    """
    raw = Path(str(file_path))
    try:
        return str(raw.relative_to(settings.BASE_DIR))
    except ValueError:
        return str(raw)


@receiver(settings_reloaded)
def on_settings_reloaded(**kwargs) -> None:
    """Bump the conf counter on every framework settings reload."""
    incr("conf", "settings_reloaded")


@receiver(provider_registered)
def on_provider_registered(**kwargs) -> None:
    """Bump the deps counter on every DI provider class definition."""
    incr("deps", "provider_registered")


@receiver(template_loaded)
def on_template_loaded(file_path: object = None, **kwargs) -> None:
    """Count the distinct page templates loaded."""
    incr("pages.template", page_key(file_path))


@receiver(context_registered)
def on_context_registered(file_path: object = None, **kwargs) -> None:
    """Count the `@context` registrations."""
    incr("pages.context", page_key(file_path))


@receiver(page_rendered)
def on_page_rendered(
    file_path: object = None, duration_ms: float | None = None, **kwargs
) -> None:
    """Count page renders and accumulate their render-time milliseconds."""
    incr("pages.rendered", page_key(file_path))
    if duration_ms is not None:
        incr("pages.duration_ms_total", page_key(file_path), by=int(duration_ms) or 1)


@receiver(route_registered)
def on_route_registered(url_path: str | None = None, **kwargs) -> None:
    """Count the routes seen during file router scans."""
    incr("urls.route", str(url_path))


@receiver(router_reloaded)
def on_router_reloaded(**kwargs) -> None:
    """Count the router rebuilds."""
    incr("urls", "router_reloaded")


@receiver(component_registered)
def on_component_registered(info: object = None, **kwargs) -> None:
    """Count the discovered components by name."""
    name = getattr(info, "name", "<unknown>")
    incr("components.registered", str(name))


@receiver(components_registered)
def on_components_registered(infos: tuple[object, ...] = (), **kwargs) -> None:
    """Count the bulk-registered components by name."""
    for info in infos:
        name = getattr(info, "name", "<unknown>")
        incr("components.registered", str(name))


@receiver(component_rendered)
def on_component_rendered(info: object = None, **kwargs) -> None:
    """Count the component render passes by name."""
    name = getattr(info, "name", "<unknown>")
    incr("components.rendered", str(name))


@receiver(action_registered)
def on_action_registered(action_name: str | None = None, **kwargs) -> None:
    """Count the form action registrations."""
    incr("forms.action_registered", str(action_name))


@receiver(action_dispatched)
def on_action_dispatched(action_name: str | None = None, **kwargs) -> None:
    """Count the form action dispatches."""
    incr("forms.action_dispatched", str(action_name))


@receiver(form_validation_failed)
def on_form_validation_failed(action_name: str | None = None, **kwargs) -> None:
    """Count the validation failures by action."""
    incr("forms.validation_failed", str(action_name))


@receiver(asset_registered)
def on_asset_registered(**kwargs) -> None:
    """Record one entry per static asset registration."""
    incr("static", "asset_registered")


@receiver(collector_finalized)
def on_collector_finalized(**kwargs) -> None:
    """Record one entry per finalized static collector."""
    incr("static", "collector_finalized")


@receiver(html_injected)
def on_html_injected(injected_bytes: int | None = None, **kwargs) -> None:
    """Record HTML injection events and accumulate the byte total."""
    incr("static", "html_injected")
    if injected_bytes:
        incr("static", "injected_bytes_total", by=int(injected_bytes))


@receiver(watch_specs_ready)
def on_watch_specs_ready(**kwargs) -> None:
    """Count the watcher reload spec resolutions."""
    incr("server", "watch_specs_ready")


@receiver(BACKEND_LOADED_SIGNALS)
def on_backend_loaded(instance: object = None, **kwargs) -> None:
    """Count every built backend under the `backends` kind, keyed by its class.

    Six settings-driven families share one `config` and `instance` payload.
    """
    incr("backends", type(instance).__name__)


@receiver(stream_subscribed)
def on_stream_subscribed(transport: str, **kwargs) -> None:
    """Count one more live stream on its transport."""
    STREAMS_OPEN.labels(transport=transport).inc()


@receiver(stream_unsubscribed)
def on_stream_unsubscribed(
    transport: str, duration: float, reason: str, **kwargs
) -> None:
    """Release the live stream, count why it ended and observe how long it lived."""
    STREAMS_OPEN.labels(transport=transport).dec()
    STREAM_UNSUBSCRIBES.labels(transport=transport, reason=str(reason)).inc()
    STREAM_DURATION.labels(transport=transport).observe(duration)


@receiver(message_published)
def on_message_published(topic: str, *, ephemeral: bool, **kwargs) -> None:
    """Count a publication by topic family, an ephemeral presence snapshot apart."""
    metric = PRESENCE_SNAPSHOTS if ephemeral else MESSAGES_PUBLISHED
    metric.labels(family=topic_family(topic)).inc()


@receiver(upstream_received)
def on_upstream_received(upstream: Upstream, **kwargs) -> None:
    """Count one upstream event by name, a set bounded by the registered handlers."""
    UPSTREAM_EVENTS.labels(name=upstream.name).inc()


@receiver(upstream_rejected)
def on_upstream_rejected(reason: UpstreamError, **kwargs) -> None:
    """Count one undelivered upstream event by reason, a closed enum.

    The topic and name the client sent are unchecked, so neither becomes a label.
    """
    UPSTREAM_REJECTED.labels(reason=str(reason)).inc()
