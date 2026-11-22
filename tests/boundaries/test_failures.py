import asyncio

import pytest
from django.core.exceptions import (
    BadRequest,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.http import Http404
from django.http.multipartparser import MultiPartParserError

from next.boundaries import contained, kind_of, shown_kind
from next.defer import ErrorKind


class TestCatchRules:
    """Every exception is contained except the ones Django answers and disconnects."""

    @pytest.mark.parametrize(
        ("exc", "before", "after"),
        [
            (ValueError("x"), True, True),
            (KeyError("x"), True, True),
            (Http404(), False, True),
            (PermissionDenied(), False, True),
            (SuspiciousOperation(), False, True),
            (BadRequest(), False, True),
            (MultiPartParserError(), False, True),
            (RequestAborted(), False, False),
            (KeyboardInterrupt(), False, False),
            (GeneratorExit(), False, False),
            (asyncio.CancelledError(), False, False),
        ],
        ids=lambda value: (
            type(value).__name__ if isinstance(value, BaseException) else None
        ),
    )
    def test_contained_before_and_after_the_first_byte(
        self, exc: BaseException, *, before: bool, after: bool
    ) -> None:
        assert contained(exc) is before
        assert contained(exc, started=True) is after

    @pytest.mark.parametrize(
        ("exc", "kind"),
        [
            (Http404(), ErrorKind.NOT_FOUND),
            (PermissionDenied(), ErrorKind.FORBIDDEN),
            (BadRequest(), ErrorKind.BAD_REQUEST),
            (SuspiciousOperation(), ErrorKind.BAD_REQUEST),
            (MultiPartParserError(), ErrorKind.BAD_REQUEST),
            (ValueError(), ErrorKind.ERROR),
        ],
    )
    def test_kind_of(self, exc: Exception, kind: ErrorKind) -> None:
        assert kind_of(exc) is kind

    @pytest.mark.parametrize(
        ("kind", "opaque", "shown"),
        [
            (ErrorKind.NOT_FOUND, True, ErrorKind.ERROR),
            (ErrorKind.FORBIDDEN, True, ErrorKind.ERROR),
            (ErrorKind.BAD_REQUEST, True, ErrorKind.ERROR),
            (ErrorKind.OVERLOADED, True, ErrorKind.ERROR),
            (ErrorKind.TIMEOUT, True, ErrorKind.TIMEOUT),
            (ErrorKind.NOT_FOUND, False, ErrorKind.NOT_FOUND),
        ],
    )
    def test_shown_kind(
        self, kind: ErrorKind, shown: ErrorKind, *, opaque: bool
    ) -> None:
        assert shown_kind(kind, opaque=opaque) is shown
