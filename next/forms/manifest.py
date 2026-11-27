"""The startup wiring of form actions and the ports the pages read them by."""

from next.manifest import Bind, Manifest
from next.ports import action_owners_slot, form_routes_slot

from . import checks, signals
from .manager import form_action_manager
from .ports import ActionOwnersImpl


manifest = Manifest(
    "forms",
    ports=(
        Bind(action_owners_slot, ActionOwnersImpl()),
        Bind(form_routes_slot, form_action_manager),
    ),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
