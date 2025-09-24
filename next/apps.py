"""Django app configuration for next-dj framework."""

from django.apps import AppConfig


class NextFrameworkConfig(AppConfig):
    """Configuration class for the next-dj Django framework app."""

    name = "next"
    verbose_name = "Next Django Framework"

    def ready(self) -> None:
        """Initialize Django checks when app is ready."""
