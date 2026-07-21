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
    form_validation_failed,
)
from next.pages.signals import context_registered, page_rendered, template_loaded
from next.server.signals import watch_specs_ready
from next.static.signals import (
    asset_registered,
    backend_loaded,
    collector_finalized,
    html_injected,
)
from next.urls.signals import route_registered, router_reloaded

from .metrics import incr


@receiver(settings_reloaded)
def on_settings_reloaded(**kwargs) -> None:
    """Conf group: bump on every framework settings reload."""
    incr("conf", "settings_reloaded")


@receiver(provider_registered)
def on_provider_registered(**kwargs) -> None:
    """Deps group: bump on every DI provider class definition."""
    incr("deps", "provider_registered")


@receiver(template_loaded)
def on_template_loaded(file_path: object = None, **kwargs) -> None:
    """Pages group: count distinct page templates loaded."""
    incr("pages.template", str(file_path))


@receiver(context_registered)
def on_context_registered(file_path: object = None, **kwargs) -> None:
    """Pages group: count `@context` registrations."""
    incr("pages.context", str(file_path))


@receiver(page_rendered)
def on_page_rendered(
    file_path: object = None, duration_ms: float | None = None, **kwargs
) -> None:
    """Pages group: count renders and accumulate render-time milliseconds."""
    incr("pages.rendered", str(file_path))
    if duration_ms is not None:
        incr("pages.duration_ms_total", str(file_path), by=int(duration_ms) or 1)


@receiver(route_registered)
def on_route_registered(url_path: str | None = None, **kwargs) -> None:
    """Urls group: count routes seen during file router scans."""
    incr("urls.route", str(url_path))


@receiver(router_reloaded)
def on_router_reloaded(**kwargs) -> None:
    """Urls group: count router rebuilds."""
    incr("urls", "router_reloaded")


@receiver(component_registered)
def on_component_registered(info: object = None, **kwargs) -> None:
    """Components group: count discovered components by name."""
    name = getattr(info, "name", "<unknown>")
    incr("components.registered", str(name))


@receiver(components_registered)
def on_components_registered(infos: tuple[object, ...] = (), **kwargs) -> None:
    """Components group: count bulk-registered components by name."""
    for info in infos:
        name = getattr(info, "name", "<unknown>")
        incr("components.registered", str(name))


@receiver(component_backend_loaded)
def on_component_backend_loaded(**kwargs) -> None:
    """Components group: count built component backends."""
    incr("components", "backend_loaded")


@receiver(component_rendered)
def on_component_rendered(info: object = None, **kwargs) -> None:
    """Components group: count component render passes by name."""
    name = getattr(info, "name", "<unknown>")
    incr("components.rendered", str(name))


@receiver(action_registered)
def on_action_registered(action_name: str | None = None, **kwargs) -> None:
    """Forms group: count action registrations."""
    incr("forms.action_registered", str(action_name))


@receiver(action_dispatched)
def on_action_dispatched(action_name: str | None = None, **kwargs) -> None:
    """Forms group: count action dispatches."""
    incr("forms.action_dispatched", str(action_name))


@receiver(form_validation_failed)
def on_form_validation_failed(action_name: str | None = None, **kwargs) -> None:
    """Forms group: count validation failures by action."""
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


@receiver(backend_loaded)
def on_static_backend_loaded(**kwargs) -> None:
    """Record one entry per built static backend."""
    incr("static", "backend_loaded")


@receiver(watch_specs_ready)
def on_watch_specs_ready(**kwargs) -> None:
    """Server group: count watcher reload spec resolutions."""
    incr("server", "watch_specs_ready")
