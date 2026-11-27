"""The startup wiring of the file router and the port other areas reach it by."""

from next.manifest import Bind, Manifest
from next.ports import route_access_slot

from . import checks, signals
from .manager import reload_app_routers, reload_router_settings
from .ports import RouteAccessImpl


manifest = Manifest(
    "urls",
    setting_changed=(reload_app_routers,),
    settings_reloaded=(reload_router_settings,),
    ports=(Bind(route_access_slot, RouteAccessImpl()),),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
