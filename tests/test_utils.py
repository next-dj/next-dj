import pytest

from next.utils import caller_source_path


def test_caller_source_path_requires_one_mode() -> None:
    """Callers must pass exactly one of the two skip strategies."""
    with pytest.raises(ValueError, match="Specify skip_while_filename_endswith"):
        caller_source_path()

    with pytest.raises(ValueError, match="only one of"):
        caller_source_path(
            skip_while_filename_endswith=("a.py",),
            skip_framework_file=("b.py", "next"),
        )
