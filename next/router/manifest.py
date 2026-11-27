"""The startup wiring of the client router, its ports, declaration and participant."""

from django.contrib.auth.signals import user_logged_in, user_logged_out

from next.manifest import Bind, Manifest
from next.ports import router_stamp_slot, zone_marking_slot

from . import checks, signals
from .declarations import router_declaration
from .lifecycle import RouterParticipant
from .navigation import action_marking, clear_on_login, clear_on_logout, stamp
from .render import RouterScripts


manifest = Manifest(
    "router",
    connects=((user_logged_in, clear_on_login), (user_logged_out, clear_on_logout)),
    ports=(Bind(router_stamp_slot, stamp), Bind(zone_marking_slot, action_marking)),
    providers=(RouterScripts(),),
    declarations=(router_declaration,),
    participants=(RouterParticipant(),),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
