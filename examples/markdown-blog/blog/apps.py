from django.apps import AppConfig


class BlogConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "blog"
    label = "blog"

    def ready(self) -> None:
        """Connect the `template_loaded` receiver so we can trace loader wins."""
        from blog import signals
