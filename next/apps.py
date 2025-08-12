from django.apps import AppConfig


class NextFrameworkConfig(AppConfig):
    name = "next"
    verbose_name = "Next Django Framework"

    def ready(self) -> None:
        from . import checks  # noqa: F401
