import pytest
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.sessions.backends.base import SessionBase

from next.identity import (
    LiveIdentity,
    SubjectBinding,
    TicketBinding,
    account_parts,
    account_subject,
    request_subject,
    session_account,
    subject_digest,
)
from next.realtime import SessionBinding, SignedCookieBinding
from next.realtime.binding import SUBJECT_SALT, opaque_subject
from next.testing import override_next_settings
from tests.support.tokens import request_for


class CookieBinding(TicketBinding):
    """A ticket binding whose credential is one cookie and whose subject echoes it."""

    def issue(self, request) -> str:
        return "issued"

    def credential(self, cookies) -> str | None:
        return cookies.get("who")

    def subject(self, credential: str) -> str | None:
        return None if credential == "nobody" else f"subject:{credential}"

    def live(self, credential: str) -> LiveIdentity | None:
        return None


class TestBindingContract:
    """Realtime tickets and boundary tokens share one binding contract."""

    def test_every_ticket_binding_is_a_subject_binding(self) -> None:
        for binding in (TicketBinding, SessionBinding, SignedCookieBinding):
            assert issubclass(binding, SubjectBinding)

    def test_a_ticket_binding_proves_what_its_handshake_would(self) -> None:
        request = request_for()
        assert CookieBinding().bound(request) is None
        request.COOKIES["who"] = "ada"
        assert CookieBinding().bound(request) == "subject:ada"
        request.COOKIES["who"] = "nobody"
        assert CookieBinding().bound(request) is None


class TestRequestSubject:
    """``request_subject`` digests what a request proves under one salt."""

    def test_no_subject_is_empty(self) -> None:
        assert request_subject(request_for(), CookieBinding(), "salt") == ""

    def test_the_digest_hides_the_subject_and_depends_on_the_salt(self) -> None:
        request = request_for()
        request.COOKIES["who"] = "ada"
        digest = request_subject(request, CookieBinding(), "salt")
        assert digest == subject_digest("subject:ada", "salt")
        assert "ada" not in digest
        assert digest != request_subject(request, CookieBinding(), "other")

    def test_it_equals_the_socket_subject_of_the_realtime_binding(self) -> None:
        request = request_for()
        request.COOKIES["sessionid"] = "k" * 32
        binding = {"TICKET_BINDING": "next.realtime.SessionBinding"}
        with override_next_settings(REALTIME=binding):
            assert request_subject(request, SessionBinding(), SUBJECT_SALT) == (
                opaque_subject("k" * 32)
            )


class TestAccounts:
    """An account credential and its subject keep one format across bindings."""

    def test_a_session_without_a_login_holds_no_account(self) -> None:
        assert session_account(SessionBase()) is None

    def test_a_signed_in_session_splits_back_into_its_parts(self) -> None:
        session = SessionBase()
        session.update(
            {
                HASH_SESSION_KEY: "hash",
                BACKEND_SESSION_KEY: "auth.Backend",
                SESSION_KEY: 7,
            }
        )
        credential = session_account(session)
        assert credential == "user:hash:auth.Backend:7"
        assert account_parts(credential) == ("hash", "auth.Backend", "7")
        assert account_subject("hash", "7") == "user:hash:7"

    @pytest.mark.parametrize("credential", [None, "", "visitor:x", "user:a:b"])
    def test_anything_else_has_no_parts(self, credential) -> None:
        assert account_parts(credential) is None
