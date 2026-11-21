import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.contrib.sessions.backends.cache import SessionStore
from django.core.exceptions import ImproperlyConfigured

from next import realtime
from next.identity import SubjectBinding, TicketBinding, account_subject
from next.suspense import BoundaryBindingError
from next.suspense.tokens import CallableBinding, NoBinding, SessionBinding, UserBinding
from next.suspense.tokens.binding import BINDINGS, build_binding
from tests.support.tokens import request_for


def tenant(request) -> str | None:
    return request.headers.get("X-Tenant")


def numeric(request) -> object:
    return 7


class _Abstract(SubjectBinding):
    pass


class TestNoBinding:
    def test_it_binds_nothing_for_every_visitor(self) -> None:
        binding = NoBinding()
        assert binding.issue(request_for()) == ""
        assert binding.bound(request_for()) == ""


class TestSessionBinding:
    """``SessionBinding`` binds the session key and refuses a visitor without one."""

    def test_it_binds_the_session_key(self) -> None:
        session = SessionStore()
        session.create()
        request = request_for()
        request.session = session
        subject = SessionBinding().issue(request)
        assert subject == f"session:{session.session_key}"
        assert SessionBinding().bound(request) == subject

    @pytest.mark.parametrize("session", [None, SessionStore()])
    def test_a_visitor_without_a_session_cannot_mint(self, session) -> None:
        request = request_for()
        if session is not None:
            request.session = session
        with pytest.raises(BoundaryBindingError, match="no session"):
            SessionBinding().issue(request)
        assert SessionBinding().bound(request) is None


class TestUserBinding:
    """``UserBinding`` binds pk and auth hash, read from the user at each request."""

    def test_an_anonymous_visitor_binds_as_anonymous(self) -> None:
        request = request_for()
        request.user = AnonymousUser()
        assert UserBinding().issue(request) == "anonymous"
        assert UserBinding().bound(request) == "anonymous"

    def test_a_request_without_a_user_cannot_mint(self) -> None:
        with pytest.raises(BoundaryBindingError, match="no user"):
            UserBinding().issue(request_for())
        assert UserBinding().bound(request_for()) is None

    def test_a_password_change_changes_the_subject(self) -> None:
        user = User(pk=3, username="ada", password="before")
        request = request_for()
        request.user = user
        before = UserBinding().issue(request)
        assert before == account_subject(user.get_session_auth_hash(), "3")
        user.password = "after"
        assert UserBinding().bound(request) != before

    def test_a_user_without_an_auth_hash_binds_its_pk(self) -> None:
        class Account:
            is_authenticated = True
            pk = 9

        request = request_for()
        request.user = Account()
        assert UserBinding().issue(request) == account_subject("", "9")


class TestCallableBinding:
    """``CallableBinding`` binds what its callable returns, `None` as a literal."""

    def test_a_value_and_none_bind_distinct_subjects(self) -> None:
        binding = CallableBinding(tenant)
        request = request_for()
        assert binding.issue(request) == "none"
        assert binding.bound(request) == "none"
        tenanted = request_for()
        tenanted.META["HTTP_X_TENANT"] = "none"
        assert binding.issue(tenanted) == "value:none"

    def test_a_value_of_another_type_is_refused(self) -> None:
        with pytest.raises(TypeError, match="not a str or None"):
            CallableBinding(numeric).issue(request_for())


class TestNeedsSessions:
    """A binding that reads the session says so, which the checks and settings read."""

    @pytest.mark.parametrize(
        "binding",
        [
            SessionBinding,
            TicketBinding,
            realtime.SessionBinding,
            realtime.SignedCookieBinding,
        ],
    )
    def test_a_session_reader_needs_sessions(self, binding: type) -> None:
        assert binding.needs_sessions

    @pytest.mark.parametrize(
        "binding", [SubjectBinding, NoBinding, UserBinding, CallableBinding]
    )
    def test_any_other_binding_does_not(self, binding: type) -> None:
        assert not binding.needs_sessions


class TestBuildBinding:
    """``build_binding`` reads a short name, a binding class or a callable path."""

    @pytest.mark.parametrize(("name", "klass"), sorted(BINDINGS.items()))
    def test_a_short_name_builds_its_binding(self, name: str, klass: type) -> None:
        assert type(build_binding(name)) is klass

    def test_a_class_path_builds_the_class(self) -> None:
        assert isinstance(build_binding("next.suspense.UserBinding"), UserBinding)

    def test_a_callable_path_is_wrapped(self) -> None:
        built = build_binding(f"{__name__}.tenant")
        assert isinstance(built, CallableBinding)
        assert built.subject is tenant

    @pytest.mark.parametrize(
        ("path", "words"),
        [
            ("nowhere.binding", "does not import"),
            ("builtins.int", "is no subclass of SubjectBinding"),
            (f"{__name__}._Abstract", "is an abstract class"),
            ("next.suspense.CallableBinding", "takes arguments"),
            ("next.suspense.tokens.binding.BINDINGS", "names no callable"),
        ],
    )
    def test_any_other_path_is_refused(self, path: str, words: str) -> None:
        with pytest.raises(ImproperlyConfigured, match=words):
            build_binding(path)
