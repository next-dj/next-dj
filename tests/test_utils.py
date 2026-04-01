from unittest.mock import patch

import pytest

from next.utils import caller_source_path, resolve_base_dir


def test_resolve_base_dir_non_path_non_str_returns_none() -> None:
    """When ``BASE_DIR`` is neither Path nor str, return None."""
    with patch("next.utils.settings") as mock_settings:
        mock_settings.BASE_DIR = object()
        assert resolve_base_dir() is None


def test_caller_source_path_requires_one_mode() -> None:
    """Callers must pass exactly one of the two skip strategies."""
    with pytest.raises(ValueError, match="Specify skip_while_filename_endswith"):
        caller_source_path()

    with pytest.raises(ValueError, match="only one of"):
        caller_source_path(
            skip_while_filename_endswith=("a.py",),
            skip_framework_file=("b.py", "next"),
        )
