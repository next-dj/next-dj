import pytest

from next.boundaries.failures import ErrorKind
from next.defer import (
    BoundaryBindingError,
    BoundaryError,
    BoundaryMintError,
    BoundaryPropsError,
    BoundarySizeError,
    BoundaryStorageError,
    BoundaryTokenError,
    BoundaryValueError,
    TokenRejection,
)
from next.defer.errors import (
    BoundaryDeadlineError,
    BoundaryRefusedError,
    BoundaryShedError,
    ExecutorStoppedError,
)


class TestBoundaryTokenError:
    """Tests for ``BoundaryTokenError``."""

    @pytest.mark.parametrize(
        "reason",
        [
            TokenRejection.EXPIRED,
            TokenRejection.VERSION,
            TokenRejection.STORAGE_MISS,
            TokenRejection.STORAGE_DOWN,
        ],
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
            "storage_down",
        ]

    @pytest.mark.parametrize("reason", list(TokenRejection))
    def test_every_reason_has_a_detail_the_message_leaves_out(
        self, reason: TokenRejection
    ) -> None:
        error = BoundaryTokenError(reason)
        assert error.detail.startswith(f"{reason}, ")
        assert error.detail not in str(error)


class TestBoundaryPropsError:
    """Tests for ``BoundaryPropsError``."""

    def test_it_is_a_value_error(self) -> None:
        msg = "the token is over MAX_BYTES"
        with pytest.raises(ValueError, match="over MAX_BYTES"):
            raise BoundaryPropsError(msg)


class TestBoundarySizeError:
    """Tests for ``BoundarySizeError``."""

    def test_it_is_a_mint_time_props_error(self) -> None:
        assert issubclass(BoundarySizeError, BoundaryPropsError)


class TestBoundaryValueError:
    """Tests for ``BoundaryValueError``."""

    def test_it_is_a_mint_time_props_error_of_its_own(self) -> None:
        assert issubclass(BoundaryValueError, BoundaryPropsError)
        assert not issubclass(BoundaryValueError, BoundarySizeError)


class TestHierarchy:
    """Every boundary error shares one root, and mint-time refusals one base."""

    @pytest.mark.parametrize(
        "error", [BoundaryMintError, BoundaryTokenError, BoundaryRefusedError]
    )
    def test_each_family_has_the_one_root(self, error: type[Exception]) -> None:
        assert issubclass(error, BoundaryError)

    @pytest.mark.parametrize(
        "error",
        [
            BoundaryPropsError,
            BoundarySizeError,
            BoundaryValueError,
            BoundaryBindingError,
            BoundaryStorageError,
        ],
    )
    def test_each_mint_time_refusal_is_a_mint_error(
        self, error: type[Exception]
    ) -> None:
        assert issubclass(error, BoundaryMintError)

    @pytest.mark.parametrize("error", [BoundaryBindingError, BoundaryStorageError])
    def test_a_refusal_beyond_the_props_is_no_props_error(
        self, error: type[Exception]
    ) -> None:
        assert not issubclass(error, BoundaryPropsError)
        assert not issubclass(error, ValueError)


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
