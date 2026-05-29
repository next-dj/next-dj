from __future__ import annotations

from pathlib import Path

import pytest

from next.components.registry import ComponentRegistry
from next.forms.backends import ActionRegistration, RegistryFormActionBackend
from tests.benchmarks.factories import build_component_info_list, noop_form_handler


_BENCH_ROOT = Path(__file__).parent


def pytest_collection_modifyitems(config, items):
    for item in items:
        if Path(item.fspath).is_relative_to(_BENCH_ROOT):
            item.add_marker(pytest.mark.perf)


@pytest.fixture()
def populated_form_backend() -> RegistryFormActionBackend:
    """``RegistryFormActionBackend`` pre-loaded with 100 registered actions."""
    backend = RegistryFormActionBackend()
    for i in range(100):
        backend.register_action(
            ActionRegistration(
                name=f"act_{i}",
                file_path=__file__,
                scope="shared",
                handler=noop_form_handler,
            )
        )
    return backend


@pytest.fixture()
def populated_component_registry(
    tmp_path: Path,
) -> tuple[ComponentRegistry, Path]:
    """``ComponentRegistry`` with 500 components rooted at ``tmp_path``."""
    registry = ComponentRegistry()
    registry.mark_as_root(tmp_path)
    registry.register_many(build_component_info_list(tmp_path, 500))
    return registry, tmp_path
