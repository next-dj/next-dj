from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from next.utils import caller_source_path, resolve_base_dir


class TestResolveBaseDir:
    """Tests for ``resolve_base_dir``."""

    def test_returns_path_when_base_dir_is_path(self) -> None:
        """``BASE_DIR`` already a ``Path`` is returned unchanged."""
        p = Path("/some/project")
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = p
            result = resolve_base_dir()
        assert result == p
        assert isinstance(result, Path)

    def test_returns_path_when_base_dir_is_string(self) -> None:
        """String ``BASE_DIR`` is converted to a ``Path``."""
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = "/some/project"
            result = resolve_base_dir()
        assert result == Path("/some/project")

    def test_returns_none_when_base_dir_is_neither_path_nor_str(self) -> None:
        """When ``BASE_DIR`` is neither Path nor str, return None."""
        with patch("next.utils.settings") as mock_settings:
            mock_settings.BASE_DIR = object()
            assert resolve_base_dir() is None

    def test_returns_none_when_base_dir_attribute_missing(self) -> None:
        """When ``BASE_DIR`` is not configured at all, return None."""
        with patch("next.utils.settings") as mock_settings:
            del mock_settings.BASE_DIR
            assert resolve_base_dir() is None


class TestCallerSourcePath:
    """Tests for ``caller_source_path``."""

    def test_raises_when_no_mode_specified(self) -> None:
        """Callers must pass exactly one of the two skip strategies."""
        with pytest.raises(ValueError, match="Specify skip_while_filename_endswith"):
            caller_source_path()

    def test_raises_when_both_modes_specified(self) -> None:
        """Passing both strategies at once raises ValueError."""
        with pytest.raises(ValueError, match="only one of"):
            caller_source_path(
                skip_while_filename_endswith=("a.py",),
                skip_framework_file=("b.py", "next"),
            )

    def test_skip_while_filename_endswith_returns_caller_file(self) -> None:
        """``skip_while_filename_endswith`` skips pytest internals and returns this file."""
        result = caller_source_path(skip_while_filename_endswith=("_pytest/python.py",))
        assert result.exists()
        assert result.suffix == ".py"

    def test_skip_framework_file_returns_caller_file(self) -> None:
        """``skip_framework_file`` skips the named framework module and returns this file."""
        result = caller_source_path(
            skip_framework_file=("_pytest/python.py", "_pytest")
        )
        assert result.exists()
        assert result.suffix == ".py"
