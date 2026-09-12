import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from next.components import FileComponentsBackend
from next.components.sources import (
    get_components_manager,
    iter_serialized_component_context_keys,
    reset_components_manager_cache,
)
from next.conf.signals import settings_reloaded
from tests.support import DummyComponentsBackend


@pytest.fixture(autouse=True)
def _clean_components_manager_cache():
    reset_components_manager_cache()
    yield
    reset_components_manager_cache()


class TestComponentsManagerCache:
    """`get_components_manager` reuses one manager per run."""

    def test_built_once_across_repeated_calls(self) -> None:
        with patch("next.components.sources.ComponentsManager") as mock_cls:
            first = get_components_manager()
            second = get_components_manager()
            third = get_components_manager()
        assert first is second is third
        assert mock_cls.call_count == 1
        assert mock_cls.return_value.reload.call_count == 1

    def test_explicit_reset_forces_rebuild(self) -> None:
        with patch("next.components.sources.ComponentsManager") as mock_cls:
            get_components_manager()
            reset_components_manager_cache()
            get_components_manager()
        assert mock_cls.call_count == 2
        assert mock_cls.return_value.reload.call_count == 2

    def test_settings_reloaded_signal_resets_cache(self) -> None:
        with patch("next.components.sources.ComponentsManager") as mock_cls:
            get_components_manager()
            settings_reloaded.send(sender=None)
            get_components_manager()
        assert mock_cls.call_count == 2


class TestSerializedComponentContextKeys:
    """iter_serialized_component_context_keys reports what a component.py declares."""

    def _backend(self, tmp_path: Path, body: str) -> FileComponentsBackend:
        """Write a composite component with `body` as its component.py."""
        comp_dir = tmp_path / "widget"
        comp_dir.mkdir()
        (comp_dir / "component.djx").write_text("<div/>")
        (comp_dir / "component.py").write_text(textwrap.dedent(body))
        return FileComponentsBackend(
            {"DIRS": [str(tmp_path)], "COMPONENTS_DIR": "_components"}
        )

    def _keys(self, *backends: object) -> list[tuple[Path, str]]:
        """Enumerate serialized keys with a components manager over `backends`."""
        manager = MagicMock()
        manager.backends = tuple(backends)
        with patch(
            "next.components.sources.get_components_manager", return_value=manager
        ):
            return list(iter_serialized_component_context_keys())

    def test_keyed_serialized_key_is_reported(self, tmp_path: Path) -> None:
        backend = self._backend(
            tmp_path,
            """
            from next.components import context


            @context("$csrf", serialize=True)
            def csrf_token():
                return {"token": "app"}
            """,
        )
        assert self._keys(backend) == [(tmp_path / "widget" / "component.py", "$csrf")]

    def test_unserialized_and_keyless_contexts_are_skipped(
        self, tmp_path: Path
    ) -> None:
        backend = self._backend(
            tmp_path,
            """
            from next.components import context


            @context("plain")
            def plain():
                return 1


            @context(serialize=True)
            def spread():
                return {"$dev": True}
            """,
        )
        assert self._keys(backend) == []

    def test_backend_without_component_files_is_skipped(self, tmp_path: Path) -> None:
        assert self._keys(DummyComponentsBackend({})) == []
