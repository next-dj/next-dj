"""Import the conventional modules of every installed app when the framework starts."""

from django.utils.module_loading import autodiscover_modules

from next.forms.autodiscover import autodiscover_forms
from next.realtime.handlers import autodiscover_handlers


def install() -> None:
    """Import `deps`, `permissions`, `forms`, `realtime` and `signals` of every app.

    A name finishes across all apps first, so a form sees the providers of every app.
    """
    autodiscover_modules("deps")
    autodiscover_modules("permissions")
    autodiscover_forms()
    autodiscover_handlers()
    autodiscover_modules("signals")


__all__ = ["install"]
