from pathlib import Path

import pytest
from django.contrib.auth.models import AnonymousUser, User
from django.http import HttpRequest, HttpResponse, HttpResponseBase

from next.forms.dispatch import FormActionDispatch
from next.forms.dispatch.permissions import _check_owner_page_access, _check_page_access
from next.forms.origin import OriginMatch
from next.forms.signals import action_dispatched, form_access_denied
from next.pages.loaders import _load_python_module, load_page_module
from next.testing import NextClient, capture_signals, envelope_of
from tests.support import routed, write_page
from tests.support.access import client_as


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


_OWNER = """
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import action


@action("owned_note")
def owned_note() -> HttpResponse:
    return HttpResponse("saved")


def render(request: HttpRequest) -> str | HttpResponseRedirect:
    if not request.user.is_authenticated:
        return HttpResponseRedirect("/login/")
    return "<h1>notes</h1>"
"""


@pytest.fixture()
def render_tree(tmp_path: Path) -> Path:
    """Write a page whose `render()` guards its action, a page below it and another."""
    root = tmp_path / "pages"
    load_page_module(write_page(root, "notes", _OWNER))
    write_page(root, "notes/child")
    write_page(root, "open")
    return root


def _match(page_path: Path | None, origin: str) -> OriginMatch:
    return OriginMatch(page_path=page_path, url_kwargs={}, origin=origin)


def _owner_denial(
    request: HttpRequest, file_path: str, match: OriginMatch, *, scope: str = "page"
) -> HttpResponseBase | None:
    return _check_owner_page_access(
        request, file_path, match, scope=scope, action_name="a", uid=None, sender=object
    )


@pytest.mark.django_db()
class TestOwnerRenderGuardsTheAction:
    """The owner page's `render()` answers its action whatever origin is posted."""

    @pytest.mark.parametrize(
        ("origin", "status"),
        [("/guarded/", 302), ("/", 403), ("/_next/csrf/", 403), (None, 400)],
    )
    def test_anonymous_submission_never_runs_the_action(
        self, next_client: NextClient, origin: str | None, status: int
    ) -> None:
        with capture_signals(action_dispatched) as recorder:
            response = _post_guarded(next_client, origin)
        assert response.status_code == status
        assert recorder.events == []

    @pytest.mark.parametrize(
        ("origin", "status"), [("/", 403), ("/_next/csrf/", 403), (None, 400)]
    )
    def test_a_member_still_needs_an_origin_in_the_subtree(
        self, next_client: NextClient, origin: str | None, status: int
    ) -> None:
        next_client.force_login(User.objects.create_user("member", password="pw"))
        with capture_signals(action_dispatched) as recorder:
            response = _post_guarded(next_client, origin)
        assert response.status_code == status
        assert recorder.events == []

    @pytest.mark.parametrize("origin", ["/", "/_next/csrf/"])
    def test_the_refusal_is_audited(self, next_client: NextClient, origin: str) -> None:
        with capture_signals(form_access_denied) as recorder:
            _post_guarded(next_client, origin)
        assert [event.kwargs["reason"] for event in recorder.events] == ["denied"]

    @pytest.mark.parametrize(
        ("kind", "origin", "status", "reasons"),
        [
            ("anonymous", "/notes/child/", 302, ["response"]),
            ("visitor", "/notes/child/", 200, []),
            ("anonymous", "/notes/", 302, ["response"]),
            ("visitor", "/open/", 403, ["denied"]),
        ],
    )
    def test_an_origin_answers_to_the_owner_render(
        self, render_tree: Path, kind: str, origin: str, status: int, reasons: list[str]
    ) -> None:
        with routed(render_tree), capture_signals(form_access_denied) as recorder:
            response = client_as(kind).post_action("owned_note", {}, origin=origin)
        assert response.status_code == status
        assert [event.kwargs["reason"] for event in recorder.events] == reasons

    @pytest.mark.parametrize("file_path", [str(_OPEN_PAGE), "/elsewhere/x.py"])
    def test_an_owner_without_render_adds_nothing(
        self, mock_http_request, file_path: str
    ) -> None:
        match = _match(None, "/_next/csrf/")
        assert _owner_denial(mock_http_request(), file_path, match) is None

    def test_a_shared_action_answers_to_no_render(self, mock_http_request) -> None:
        match = _match(_OPEN_PAGE, "/board/")
        denial = _owner_denial(
            mock_http_request(), str(_GUARDED_PAGE), match, scope="shared"
        )
        assert denial is None

    def test_an_origin_through_a_symlink_runs_the_render(self, rf, tmp_path) -> None:
        alias = tmp_path / "alias"
        alias.symlink_to(_GUARDED_PAGE.parent, target_is_directory=True)
        match = _match(alias / "child" / "page.py", "/alias/child/")
        request = rf.post("/_next/form/x/")
        request.user = AnonymousUser()
        denial = _owner_denial(request, str(_GUARDED_PAGE), match)
        assert denial is not None
        assert denial["Location"] == "/login/"


class TestPageAccessSkipsWhatItCannotAddress:
    """An origin that names no page leaves the origin check with nothing to run."""

    def test_origin_without_a_page_path_allows(self, mock_http_request) -> None:
        denial = _check_page_access(
            mock_http_request(),
            _match(None, "/elsewhere/"),
            action_name="a",
            uid=None,
            sender=object,
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
