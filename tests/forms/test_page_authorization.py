from pathlib import Path

import pytest
from django.contrib.auth.models import User
from django.http import HttpResponse

from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.permissions import _check_page_access
from next.forms.origin import OriginMatch
from next.forms.signals import form_access_denied
from next.pages.loaders import _load_python_module
from next.testing import NextClient, capture_signals, envelope_of


_SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"
_GUARDED_PAGE = _SITE_PAGES / "guarded" / "page.py"


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
        response = _post_note(next_client, "x" * 20, partial=True, zones="guarded-zone")
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
        response = _post_note(next_client, "x" * 20, partial=True, zones="guarded-zone")
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["guarded-zone"]

    def test_valid_submission_runs_the_success_funnel(
        self, next_client: NextClient, member: User
    ) -> None:
        response = _post_note(next_client, "ok")
        assert response.status_code == 200
        assert b"classified" in response.content


class TestPageAccessSkipsWhatItCannotAddress:
    """An origin that names no page leaves the page check with nothing to run."""

    def test_missing_origin_match_allows(self, mock_http_request) -> None:
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
