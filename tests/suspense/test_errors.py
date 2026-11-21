import pytest

from next.defer import ErrorKind
from next.suspense import (
    BoundaryBindingError,
    BoundaryDeadlineError,
    BoundaryPropsError,
    BoundaryRefusedError,
    BoundaryShedError,
    BoundaryTokenError,
    ExecutorStoppedError,
    TokenRejection,
)


class TestBoundaryTokenError:
    """Tests for ``BoundaryTokenError``."""

    @pytest.mark.parametrize(
        "reason",
        [TokenRejection.EXPIRED, TokenRejection.VERSION, TokenRejection.STORAGE_MISS],
    )
    def test_a_stale_token_answers_with_a_reload(self, reason: TokenRejection) -> None:
        error = BoundaryTokenError(reason)
        assert error.reason is reason
        assert error.expired
        assert str(error) == "The boundary token expired."

    @pytest.mark.parametrize(
        "reason",
        [
            TokenRejection.MALFORMED,
            TokenRejection.SIGNATURE,
            TokenRejection.BINDING,
            TokenRejection.URL_MISMATCH,
            TokenRejection.TOO_LARGE,
        ],
    )
    def test_any_other_reason_answers_with_a_refusal(
        self, reason: TokenRejection
    ) -> None:
        error = BoundaryTokenError(reason)
        assert not error.expired
        assert str(error) == "The boundary token was refused."
        assert str(reason) not in str(error)

    def test_the_reasons_read_as_their_wire_names(self) -> None:
        assert [str(reason) for reason in TokenRejection] == [
            "malformed",
            "signature",
            "expired",
            "version",
            "binding",
            "url_mismatch",
            "too_large",
            "storage_miss",
        ]


class TestBoundaryPropsError:
    """Tests for ``BoundaryPropsError``."""

    def test_it_is_a_value_error(self) -> None:
        msg = "the token is over MAX_BYTES"
        with pytest.raises(ValueError, match="over MAX_BYTES"):
            raise BoundaryPropsError(msg)


class TestBoundaryBindingError:
    """Tests for ``BoundaryBindingError``."""

    def test_it_is_a_mint_time_props_error(self) -> None:
        assert issubclass(BoundaryBindingError, BoundaryPropsError)


class TestRefusals:
    """Tests for the errors of a job settled without running."""

    @pytest.mark.parametrize(
        ("error", "kind", "words"),
        [
            (BoundaryShedError, ErrorKind.OVERLOADED, "shed"),
            (BoundaryDeadlineError, ErrorKind.TIMEOUT, "deadline"),
            (ExecutorStoppedError, ErrorKind.OVERLOADED, "executor stopped"),
        ],
    )
    def test_each_refusal_names_the_kind_it_settles_as(
        self, error: type[BoundaryRefusedError], kind: ErrorKind, words: str
    ) -> None:
        raised = error()
        assert isinstance(raised, BoundaryRefusedError)
        assert raised.kind is kind
        assert words in str(raised)
