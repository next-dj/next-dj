from typing import ClassVar

from django.apps import AppConfig


class LibraryConfig(AppConfig):
    name = "library"
    label = "library"
    verbose_name = "Library"
    mounts: ClassVar[dict[str, str]] = {"book_pages": "library/book/<int:book>/"}
