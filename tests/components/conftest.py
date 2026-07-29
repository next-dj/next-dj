from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from next.components import ComponentInfo, FileComponentsBackend, components_manager
from next.components.signals import (
    component_backend_loaded,
    component_registered,
    component_rendered,
    components_registered,
)


@pytest.fixture()
def min_component_config() -> dict[str, object]:
    """Minimal FileComponentsBackend configuration."""
    return {"DIRS": [], "COMPONENTS_DIR": "_components"}


@pytest.fixture()
def installed_file_backend(
    min_component_config: dict[str, object],
) -> Generator[FileComponentsBackend, None, None]:
    """Run the shared manager over one file backend and restore it afterwards.

    The manager is told the settings were read, otherwise the first access
    reloads from settings and drops the installed backend.
    """
    backend = FileComponentsBackend(dict(min_component_config))
    saved_backends = components_manager._backends
    saved_loaded = components_manager._loaded
    saved_folders = components_manager._walk_registered_folders
    components_manager._backends = [backend]
    components_manager._loaded = True
    components_manager._walk_registered_folders = set()
    try:
        yield backend
    finally:
        components_manager._backends = saved_backends
        components_manager._loaded = saved_loaded
        components_manager._walk_registered_folders = saved_folders


@pytest.fixture()
def component_info_factory(tmp_path: Path) -> Callable[..., ComponentInfo]:
    """Return a factory that builds ``ComponentInfo`` objects rooted at ``tmp_path``."""

    def _factory(
        name: str = "card",
        *,
        scope_relative: str = "",
        template_name: str | None = None,
        module_name: str | None = None,
        is_simple: bool = True,
        scope_root: Path | None = None,
        template_path: Path | None = None,
        module_path: Path | None = None,
    ) -> ComponentInfo:
        root = scope_root if scope_root is not None else tmp_path
        if template_path is None and template_name is not None:
            template_path = root / template_name
        if module_path is None and module_name is not None:
            module_path = root / module_name
        return ComponentInfo(
            name=name,
            scope_root=root,
            scope_relative=scope_relative,
            template_path=template_path,
            module_path=module_path,
            is_simple=is_simple,
        )

    return _factory


@pytest.fixture()
def capture_component_registered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_registered`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    component_registered.connect(_listener)
    try:
        yield events
    finally:
        component_registered.disconnect(_listener)


@pytest.fixture()
def capture_components_registered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``components_registered`` (plural) signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    components_registered.connect(_listener)
    try:
        yield events
    finally:
        components_registered.disconnect(_listener)


@pytest.fixture()
def capture_component_backend_loaded() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_backend_loaded`` events with their class sender."""
    events: list[dict[str, Any]] = []

    def _listener(
        sender: object,
        config: dict[str, Any] | None = None,
        instance: object = None,
        **kwargs,
    ) -> None:
        events.append(
            {"sender": sender, "config": config, "instance": instance, **kwargs}
        )

    component_backend_loaded.connect(_listener)
    try:
        yield events
    finally:
        component_backend_loaded.disconnect(_listener)


@pytest.fixture()
def capture_component_rendered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_rendered`` signal events."""
    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs) -> None:
        events.append({"sender": sender, **kwargs})

    component_rendered.connect(_listener)
    try:
        yield events
    finally:
        component_rendered.disconnect(_listener)
