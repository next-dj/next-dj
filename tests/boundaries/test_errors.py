import pytest

from next.boundaries import BoundaryOptionError
from next.boundaries.options import BoundaryOptions
from next.errors import DurationError


class TestBoundaryOptionError:
    """A misfit option is a declaration error, wrapping a misfit duration."""

    def test_an_option_error_stays_a_declaration_error(self) -> None:
        assert issubclass(BoundaryOptionError, ValueError)

    def test_a_misfit_timeout_wraps_the_duration_error(self) -> None:
        with pytest.raises(BoundaryOptionError, match="duration literal") as caught:
            BoundaryOptions.parse(timeout="soon")
        assert isinstance(caught.value.__cause__, DurationError)
