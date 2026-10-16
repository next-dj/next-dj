from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.apps import AppConfig, apps as django_apps

from tests.support.patches import importable_dir


if TYPE_CHECKING:
    from collections.abc import Callable, Generator
    from pathlib import Path


type AppFactory = Callable[..., str]


@contextmanager
def throwaway_apps(
    directory: Path, *, keep_installed: bool = True
) -> Generator[AppFactory, None, None]:
    """Yield a factory writing app packages under `directory` that count as installed.

    Turning `keep_installed` off hides the real apps, so discovery walks these alone.
    """
    made: list[AppConfig] = []
    original = django_apps.get_app_configs() if keep_installed else []

    def make(**bodies: str) -> str:
        name = f"discovered_{uuid.uuid4().hex}"
        package = directory / name
        package.mkdir()
        (package / "__init__.py").write_text("LOADS = []\n")
        for module, body in bodies.items():
            (package / f"{module}.py").write_text(body)
        made.append(AppConfig.create(name))
        return name

    with (
        importable_dir(directory),
        patch.object(
            django_apps, "get_app_configs", side_effect=lambda: [*original, *made]
        ),
    ):
        yield make
