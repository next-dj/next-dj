from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpResponse

from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.permissions import _check_owner_page_access, _check_page_access
from next.forms.origin import OriginMatch
from next.forms.signals import action_dispatched, form_access_denied
from next.pages.loaders import _load_python_module
from next.testing import NextClient, capture_signals, envelope_of


_SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"
_GUARDED_PAGE = _SITE_PAGES / "guarded" / "page.py"
_OPEN_PAGE = _SITE_PAGES / "board_forms" / "page.py"


@pytest.fixture(autouse=True)
def _guarded_page_action() -> None:
    """Register the guarded page's form, dropped again by the registry snapshot."""
    _load_python_module(_GUARDED_PAGE)


@pytest.fixture()
def next_client() -> NextClient:
    """Test client that posts form fields manually, without CSRF checks."""
    return NextClient(enforce_csrf_checks=False)


def _post_note(client: NextClient, note: str, **kwargs) -> HttpResponse:
    return client.post_action(
        "guarded_note_form", {"note": note}, origin="/guarded/", **kwargs
    )


def _post_guarded(client: NextClient, origin: str | None) -> HttpResponse:
    if origin is None:
        url = client.get_action_url("guarded_note_form")
        return client.post(url, {"note": "ok"})
    return client.post_action("guarded_note_form", {"note": "ok"}, origin=origin)


@pytest.mark.django_db()
class TestAnonymousSubmissionIsRefusedByThePage:
    """The origin page's own short-circuit answers a submission it would refuse."""

    def test_invalid_submission_gets_the_pages_redirect(
        self, next_client: NextClient
    ) -> None:
        response = _post_note(next_client, "x" * 20)
        assert response.status_code == 302
        assert response["Location"] == "/login/"

    def test_valid_submission_gets_the_pages_redirect(
        self, next_client: NextClient
    ) -> None:
        response = _post_note(next_client, "ok")
        assert response.status_code == 302
        assert response["Location"] == "/login/"

    def test_no_page_context_value_reaches_the_body(
        self, next_client: NextClient
    ) -> None:
        response = _post_note(next_client, "x" * 20)
        assert b"classified" not in response.content

    def test_partial_submission_is_refused_the_same_way(
        self, next_client: NextClient
    ) -> None:
        response = _post_note(next_client, "x" * 20, partial=True)
        assert response.status_code == 302
        assert response["Location"] == "/login/"

    def test_zone_morph_is_refused_before_the_zone_renders(
        self, next_client: NextClient
    ) -> None:
        response = _post_note(next_client, "x" * 20, partial="guarded-zone")
        assert response.status_code == 302
        assert b"classified" not in response.content

    def test_validate_probe_is_refused(self, next_client: NextClient) -> None:
        response = _post_note(
            next_client, "x" * 20, partial=True, headers={"X-Next-Validate": "note"}
        )
        assert response.status_code == 302


@pytest.mark.django_db()
class TestAuthorizedSubmissionIsUntouched:
    """A visitor the page would serve keeps the whole re-render behaviour."""

    @pytest.fixture()
    def member(self, next_client: NextClient) -> User:
        user = User.objects.create_user("member", password="pw")
        next_client.force_login(user)
        return user

    def test_invalid_submission_re_renders_the_origin(
        self, next_client: NextClient, member: User
    ) -> None:
        response = _post_note(next_client, "x" * 20)
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    def test_re_render_carries_the_page_context(
        self, next_client: NextClient, member: User
    ) -> None:
        response = _post_note(next_client, "x" * 20)
        assert b"classified" in response.content

    def test_zone_morph_renders_the_guarded_zone(
        self, next_client: NextClient, member: User
    ) -> None:
        response = _post_note(next_client, "x" * 20, partial="guarded-zone")
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["guarded-zone"]

    def test_valid_submission_runs_the_success_funnel(
        self, next_client: NextClient, member: User
    ) -> None:
        response = _post_note(next_client, "ok")
        assert response.status_code == 200
        assert b"classified" in response.content


@pytest.mark.django_db()
class TestOwnerRenderGuardsTheAction:
    """The owner page's `render()` answers its action whatever origin is posted."""

    @pytest.mark.parametrize(
        ("origin", "status"),
        [("/guarded/", 302), ("/", 403), ("/_next/csrf/", 400), (None, 400)],
    )
    def test_anonymous_submission_never_runs_the_action(
        self, next_client: NextClient, origin: str | None, status: int
    ) -> None:
        with capture_signals(action_dispatched) as recorder:
            response = _post_guarded(next_client, origin)
        assert response.status_code == status
        assert recorder.events == []

    @pytest.mark.parametrize("origin", ["/", None])
    def test_a_member_still_needs_an_origin_in_the_subtree(
        self, next_client: NextClient, origin: str | None
    ) -> None:
        next_client.force_login(User.objects.create_user("member", password="pw"))
        with capture_signals(action_dispatched) as recorder:
            response = _post_guarded(next_client, origin)
        assert response.status_code == (403 if origin else 400)
        assert recorder.events == []

    def test_the_refusal_is_audited(self, next_client: NextClient) -> None:
        with capture_signals(form_access_denied) as recorder:
            _post_guarded(next_client, "/")
        assert [event.kwargs["reason"] for event in recorder.events] == ["denied"]

    def test_an_origin_below_the_owner_runs_its_render(self, rf) -> None:
        match = OriginMatch(
            page_path=_GUARDED_PAGE.parent / "child" / "page.py",
            url_kwargs={},
            origin="/guarded/child/",
        )
        request = rf.post("/_next/form/x/")
        request.user = AnonymousUser()
        with capture_signals(form_access_denied) as recorder:
            denial = _check_owner_page_access(
                request,
                str(_GUARDED_PAGE),
                match,
                action_name="a",
                uid=None,
                sender=object,
            )
        assert denial is not None
        assert denial["Location"] == "/login/"
        assert [event.kwargs["reason"] for event in recorder.events] == ["response"]

    def test_a_member_below_the_owner_is_admitted(self, rf) -> None:
        match = OriginMatch(
            page_path=_GUARDED_PAGE.parent / "child" / "page.py",
            url_kwargs={},
            origin="/guarded/child/",
        )
        request = rf.post("/_next/form/x/")
        request.user = User(username="member")
        denial = _check_owner_page_access(
            request, str(_GUARDED_PAGE), match, action_name="a", uid=None, sender=object
        )
        assert denial is None

    @pytest.mark.parametrize("file_path", [None, str(_OPEN_PAGE), "/elsewhere/x.py"])
    def test_an_owner_without_render_adds_nothing(
        self, mock_http_request, file_path: str | None
    ) -> None:
        denial = _check_owner_page_access(
            mock_http_request(),
            file_path,
            None,
            action_name="a",
            uid=None,
            sender=object,
        )
        assert denial is None


class TestPageAccessSkipsWhatItCannotAddress:
    """An origin that names no page leaves the origin check with nothing to run."""

    def test_missing_origin_match_leaves_the_owner_check(
        self, mock_http_request
    ) -> None:
        denial = _check_page_access(
            mock_http_request(), None, action_name="a", uid=None, sender=object
        )
        assert denial is None

    def test_origin_without_a_page_path_allows(self, mock_http_request) -> None:
        match = OriginMatch(page_path=None, url_kwargs={}, origin="/elsewhere/")
        denial = _check_page_access(
            mock_http_request(), match, action_name="a", uid=None, sender=object
        )
        assert denial is None


@pytest.mark.django_db()
class TestPageDenialIsAudited:
    """A refusal at the page layer reaches the same audit signal as a hook denial."""

    def test_the_denial_emits_its_own_layer(self, next_client: NextClient) -> None:
        with capture_signals(form_access_denied) as recorder:
            _post_note(next_client, "x" * 20)
        assert [event.kwargs["layer"] for event in recorder.events] == ["page"]

    def test_the_denial_names_the_action_and_the_sender(
        self, next_client: NextClient
    ) -> None:
        with capture_signals(form_access_denied) as recorder:
            _post_note(next_client, "x" * 20)
        event = recorder.events[0]
        assert event.sender is FormActionDispatch
        assert event.kwargs["action_name"] == "guarded_note_form"
        assert event.kwargs["reason"] == "response"

    def test_an_authorized_submission_emits_nothing(
        self, next_client: NextClient
    ) -> None:
        user = User.objects.create_user("auditor", password="pw")
        next_client.force_login(user)
        with capture_signals(form_access_denied) as recorder:
            _post_note(next_client, "x" * 20)
        assert recorder.events == []
