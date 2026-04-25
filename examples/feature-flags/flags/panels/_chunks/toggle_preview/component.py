from flags.models import Flag
from flags.providers import DFlag

from next.components import component


@component.context("state")
def _state(flag: DFlag[Flag]) -> dict[str, str]:
    if flag.enabled:
        return {"label": "on", "classes": "bg-emerald-100 text-emerald-800"}
    return {"label": "off", "classes": "bg-slate-100 text-slate-600"}
