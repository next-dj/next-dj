"""Import the conventional modules of every installed app when the framework starts."""

from django.utils.module_loading import autodiscover_modules

from next.forms.autodiscover import autodiscover_forms


def install() -> None:
    """Import `deps`, `permissions`, `forms` and `signals` of every app, in that order.

    A name finishes across all apps first, so a form sees the providers of every app.
    """
    autodiscover_modules("deps")
    autodiscover_modules("permissions")
    autodiscover_forms()
    autodiscover_modules("signals")


__all__ = ["install"]
