from __future__ import annotations

from contextlib import ExitStack, contextmanager
from typing import TYPE_CHECKING

from django.conf import settings
from django.test import override_settings

from tests.support.helpers import file_router_config_entry
from tests.support.pages import write_page
from tests.support.patches import importable_dir


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable, Mapping
    from pathlib import Path


_URLCONF_HEAD = (
    "from django.urls import include, path\n\n"
    "from next.urls import pages\n\n\n"
    "urlpatterns = [\n"
)


def write_app(
    root: Path,
    name: str,
    trees: Mapping[str, Iterable[str]] | None = None,
    **declared: object,
) -> Path:
    """Write an importable app whose config class declares `declared` as attributes.

    `trees` maps each app subdirectory to the page trails written under it.
    """
    app = root
    for part in name.split("."):
        app /= part
        app.mkdir(parents=True, exist_ok=True)
        (app / "__init__.py").touch()
    attributes = "".join(f"    {key} = {value!r}\n" for key, value in declared.items())
    (app / "apps.py").write_text(
        "from django.apps import AppConfig\n\n\n"
        "class Config(AppConfig):\n"
        f"    name = {name!r}\n"
        f"{attributes}"
    )
    for subdir, trails in ({"pages": ("",)} if trees is None else trees).items():
        (app / subdir).mkdir(parents=True, exist_ok=True)
        for trail in trails:
            write_page(app / subdir, trail)
    return app


def write_urlconf(root: Path, name: str, *entries: str) -> str:
    """Write a URLconf whose `urlpatterns` holds `entries` and return its name."""
    body = "".join(f"    {entry},\n" for entry in entries)
    (root / f"{name}.py").write_text(f"{_URLCONF_HEAD}{body}]\n")
    return name


@contextmanager
def installed_apps(
    root: Path,
    *names: str,
    urlconf: str | None = None,
    dirs: Iterable[Path] = (),
    framework: Mapping[str, object] | None = None,
    **overrides: object,
) -> Generator[None, None, None]:
    """Install the apps under `root` behind one app-tree router for the block.

    The URLconf is imported on first resolve, so its `pages()` calls find the apps.
    """
    entry = file_router_config_entry(app_dirs=True, dirs=list(dirs))
    with ExitStack() as stack:
        stack.enter_context(importable_dir(root))
        stack.enter_context(
            override_settings(
                INSTALLED_APPS=[*settings.INSTALLED_APPS, *names],
                NEXT_FRAMEWORK={"PAGE_BACKENDS": [entry], **(framework or {})},
                **overrides,
            )
        )
        if urlconf is not None:
            stack.enter_context(override_settings(ROOT_URLCONF=urlconf))
        yield
