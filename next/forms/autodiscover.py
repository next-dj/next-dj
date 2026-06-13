"""Import every `<app>.forms` module so shared forms register on startup."""

from django.utils.module_loading import autodiscover_modules

from next.conf import next_framework_settings


def autodiscover_forms() -> None:
    """Import the `forms` submodule of each installed app."""
    if not next_framework_settings.FORM_AUTODISCOVER:
        return
    autodiscover_modules("forms")


__all__ = ["autodiscover_forms"]
