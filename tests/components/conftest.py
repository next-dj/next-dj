from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

import pytest

from next.components import ComponentInfo


@pytest.fixture()
def min_component_config() -> dict[str, object]:
    """Minimal FileComponentsBackend configuration."""
    return {"DIRS": [], "COMPONENTS_DIR": "_components"}


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
    from next.components.signals import component_registered

    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    component_registered.connect(_listener)
    try:
        yield events
    finally:
        component_registered.disconnect(_listener)


@pytest.fixture()
def capture_component_backend_loaded() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_backend_loaded`` signal events."""
    from next.components.signals import component_backend_loaded

    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    component_backend_loaded.connect(_listener)
    try:
        yield events
    finally:
        component_backend_loaded.disconnect(_listener)


@pytest.fixture()
def capture_component_rendered() -> Generator[list[dict[str, Any]], None, None]:
    """Capture ``component_rendered`` signal events."""
    from next.components.signals import component_rendered

    events: list[dict[str, Any]] = []

    def _listener(sender: object, **kwargs: object) -> None:
        events.append({"sender": sender, **kwargs})

    component_rendered.connect(_listener)
    try:
        yield events
    finally:
        component_rendered.disconnect(_listener)
