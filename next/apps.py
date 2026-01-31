"""Django app configuration for next-dj framework."""

from django.apps import AppConfig


class NextFrameworkConfig(AppConfig):
    """Configuration class for the next-dj Django framework app."""

    name = "next"
    verbose_name = "Next Django Framework"

    def ready(self) -> None:
        """Initialize Django checks and form builtins when app is ready."""
        from django.conf import settings

        builtins = list(settings.TEMPLATES[0].get("OPTIONS", {}).get("builtins", []))
        if "next.templatetags.forms" not in builtins:
            builtins.append("next.templatetags.forms")
            settings.TEMPLATES[0].setdefault("OPTIONS", {})["builtins"] = builtins
