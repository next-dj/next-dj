"""The startup wiring of the settings area, which reloads first on a change."""

from django.core.signals import request_started

from next.manifest import Manifest

from . import checks, signals
from .settings import refuse_mistyped, reload_on_setting_changed


manifest = Manifest(
    "conf",
    setting_changed=(reload_on_setting_changed,),
    connects=((request_started, refuse_mistyped),),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
