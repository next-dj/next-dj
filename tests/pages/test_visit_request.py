from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.test import RequestFactory
from django.test.utils import override_script_prefix

from next.csrf import NONCE_ATTR, nonce_minted
from next.deps import REQUEST_DEP_CACHE_ATTR
from next.pages import page
from next.pages.loaders import _load_python_module
from next.pages.metadata.resolve import RESOLVED_METADATA_ATTR
from next.pages.responses import (
    RENDER_FLAGS,
    ResponsePolicy,
    cache_control,
    cookie_varies,
    finish_response,
    personal_render,
)
from next.pages.visits import HANDED_BACK, hand_back, visit_request, visiting
from next.testing import NextClient, envelope_of
from tests.support.pages import write_page


_SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"
_SHAPED_PAGE = _SITE_PAGES / "shaped" / "page.py"
_ACTION_PATH = "/_next/form/uid/"


@pytest.fixture()
def dispatch_post(rf: RequestFactory):
    """Return a POST to the dispatch endpoint, the shape an origin is posted with."""
    return rf.post(_ACTION_PATH, data={"note": "hi"}, QUERY_STRING="stale=1")


class TestVisitRequestPresentsTheOriginUrl:
    """`visit_request` restates a live request as a GET of the page it authorizes."""

    def test_method_becomes_get(self, dispatch_post) -> None:
        assert visit_request(dispatch_post, "/notes/?page=2").method == "GET"

    def test_query_comes_from_the_url(self, dispatch_post) -> None:
        visit = visit_request(dispatch_post, "/notes/?page=2&tag=a")
        assert visit.GET.dict() == {"page": "2", "tag": "a"}

    def test_path_comes_from_the_url(self, dispatch_post) -> None:
        visit = visit_request(dispatch_post, "/notes/?page=2")
        assert (visit.path, visit.path_info) == ("/notes/", "/notes/")

    def test_encoded_path_decodes_like_request_path(self, dispatch_post) -> None:
        visit = visit_request(dispatch_post, "/groups/a%3Fb/?q=1")
        assert (visit.path, visit.path_info) == ("/groups/a?b/", "/groups/a?b/")
        assert visit.GET.dict() == {"q": "1"}

    def test_full_path_reassembles_the_url(self, dispatch_post) -> None:
        visit = visit_request(dispatch_post, "/notes/?page=2")
        assert visit.get_full_path() == "/notes/?page=2"

    def test_post_is_empty(self, dispatch_post) -> None:
        assert visit_request(dispatch_post, "/notes/").POST.dict() == {}

    def test_resolver_match_is_dropped(self, dispatch_post) -> None:
        dispatch_post.resolver_match = object()
        assert visit_request(dispatch_post, "/notes/").resolver_match is None

    def test_meta_restates_the_visit(self, dispatch_post) -> None:
        visit = visit_request(dispatch_post, "/notes/?page=2")
        assert visit.META["REQUEST_METHOD"] == "GET"
        assert visit.META["QUERY_STRING"] == "page=2"
        assert visit.META["PATH_INFO"] == "/notes/"

    def test_the_live_request_is_untouched(self, dispatch_post) -> None:
        visit_request(dispatch_post, "/notes/?page=2")
        assert dispatch_post.method == "POST"
        assert dispatch_post.path == _ACTION_PATH
        assert dispatch_post.META["QUERY_STRING"] == "stale=1"

    def test_session_and_user_ride_along(self, dispatch_post) -> None:
        dispatch_post.user = "carol"
        dispatch_post.session = {"k": "v"}
        visit = visit_request(dispatch_post, "/notes/")
        assert (visit.user, visit.session) == ("carol", {"k": "v"})

    def test_the_dispatch_cache_stays_with_the_live_request(
        self, dispatch_post
    ) -> None:
        cache = {"board": "origin"}
        setattr(dispatch_post, REQUEST_DEP_CACHE_ATTR, cache)
        visit = visit_request(dispatch_post, "/boards/2/")
        assert not hasattr(visit, REQUEST_DEP_CACHE_ATTR)
        assert getattr(dispatch_post, REQUEST_DEP_CACHE_ATTR) is cache


class TestVisitRequestWithoutAUrl:
    """A caller that knows no URL still asks the page as a GET."""

    def test_method_becomes_get(self, dispatch_post) -> None:
        assert visit_request(dispatch_post, None).method == "GET"

    def test_query_is_emptied(self, dispatch_post) -> None:
        assert visit_request(dispatch_post, None).GET.dict() == {}

    def test_path_is_left_alone(self, dispatch_post) -> None:
        assert visit_request(dispatch_post, None).path == _ACTION_PATH


class TestVisitRequestUnderAScriptPrefix:
    """A sub-path deployment strips its mount point from `path_info`."""

    def test_path_info_drops_the_prefix(self, dispatch_post) -> None:
        with override_script_prefix("/app/"):
            visit = visit_request(dispatch_post, "/app/notes/")
        assert (visit.path, visit.path_info) == ("/app/notes/", "/notes/")


@pytest.fixture()
def shaped_client() -> NextClient:
    """Register the shape-guarded page's form and return a client for its action."""
    _load_python_module(_SHAPED_PAGE)
    return NextClient(enforce_csrf_checks=False)


def _post_note(client: NextClient, origin: str, **kwargs) -> HttpResponse:
    return client.post_action(
        "shaped_note_form", {"note": "x" * 20}, origin=origin, **kwargs
    )


@pytest.mark.django_db()
class TestShapeGuardedOriginAnswersAsOnAVisit:
    """A `render()` reading the request shape sees the origin page, not the endpoint."""

    def test_the_open_origin_is_served(self, shaped_client: NextClient) -> None:
        response = _post_note(shaped_client, "/shaped/?token=open")
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    def test_the_open_origin_re_renders_the_page_context(
        self, shaped_client: NextClient
    ) -> None:
        assert b"classified" in _post_note(shaped_client, "/shaped/?token=open").content

    def test_the_open_origin_morphs_its_zone(self, shaped_client: NextClient) -> None:
        response = _post_note(
            shaped_client, "/shaped/?token=open", partial="shaped-zone"
        )
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["shaped-zone"]

    def test_the_closed_origin_is_refused(self, shaped_client: NextClient) -> None:
        response = _post_note(shaped_client, "/shaped/")
        assert response.status_code == 302
        assert response["Location"] == "/closed/"

    def test_the_closed_origin_leaks_no_page_context(
        self, shaped_client: NextClient
    ) -> None:
        assert b"classified" not in _post_note(shaped_client, "/shaped/").content


_MARKING_RENDER = """
from django.http import HttpRequest, HttpResponseRedirect
from django.middleware.csrf import get_token

from next.csrf import NONCE_ATTR
from next.pages.responses import mark_bound_render, vary_on_cookie


def render(request: HttpRequest) -> HttpResponseRedirect:
    get_token(request)
    vary_on_cookie(request)
    mark_bound_render(request)
    setattr(request, NONCE_ATTR, "visit-nonce")
    return HttpResponseRedirect("/closed/")
"""

_QUIET_RENDER = """
def render() -> str:
    return "<p>open</p>"
"""


def _fail_a_visit(request: HttpRequest) -> None:
    with visiting(request, "/notes/") as visit:
        visit.META["CSRF_COOKIE_NEEDS_UPDATE"] = True
        raise LookupError


def _authorize(file_path: Path, request: HttpRequest) -> HttpResponse:
    denial, _dynamic = page.authorization_outcome(file_path, request, "/marked/")
    assert isinstance(denial, HttpResponse)
    return denial


class TestVisitHandsBackWhatItsRenderMarked:
    """A flag the render of a visit sets reaches the response of the live request."""

    def test_every_render_flag_reaches_the_live_request(
        self, tmp_path: Path, dispatch_post: HttpRequest
    ) -> None:
        _authorize(write_page(tmp_path, "", _MARKING_RENDER), dispatch_post)
        assert all(getattr(dispatch_post, name) is True for name in RENDER_FLAGS)
        assert personal_render(dispatch_post)
        assert cookie_varies(dispatch_post)

    def test_the_minted_nonce_reaches_the_live_request(
        self, tmp_path: Path, dispatch_post: HttpRequest
    ) -> None:
        _authorize(write_page(tmp_path, "", _MARKING_RENDER), dispatch_post)
        assert getattr(dispatch_post, NONCE_ATTR) == "visit-nonce"
        assert nonce_minted(dispatch_post)

    def test_a_read_token_sets_the_cookie_of_the_live_response(
        self, tmp_path: Path, dispatch_post: HttpRequest
    ) -> None:
        file_path = write_page(tmp_path, "", _MARKING_RENDER)
        middleware = CsrfViewMiddleware(lambda request: _authorize(file_path, request))
        response = middleware(dispatch_post)
        assert settings.CSRF_COOKIE_NAME in response.cookies
        assert "Cookie" in response["Vary"]

    def test_a_shared_cache_of_the_live_response_turns_private(
        self, tmp_path: Path, dispatch_post: HttpRequest
    ) -> None:
        file_path = write_page(tmp_path, "", _MARKING_RENDER)
        _authorize(file_path, dispatch_post)
        policy = ResponsePolicy(cache=cache_control({"public": True, "max_age": 60}))
        response = finish_response(HttpResponse("x"), policy, dispatch_post, file_path)
        assert "private" in response["Cache-Control"]
        assert "Cookie" in response["Vary"]

    def test_a_render_that_marks_nothing_leaves_the_live_request_alone(
        self, tmp_path: Path, dispatch_post: HttpRequest
    ) -> None:
        denial, _dynamic = page.authorization_outcome(
            write_page(tmp_path, "", _QUIET_RENDER), dispatch_post, "/marked/"
        )
        assert denial is None
        assert not any(hasattr(dispatch_post, name) for name in HANDED_BACK)
        assert "CSRF_COOKIE_NEEDS_UPDATE" not in dispatch_post.META

    def test_what_belongs_to_the_visit_stays_on_it(
        self, dispatch_post: HttpRequest
    ) -> None:
        visit = visit_request(dispatch_post, "/notes/")
        setattr(visit, RESOLVED_METADATA_ATTR, object())
        hand_back(visit, dispatch_post)
        assert not hasattr(dispatch_post, RESOLVED_METADATA_ATTR)
        assert dispatch_post.method == "POST"

    def test_a_failing_render_still_hands_back(
        self, dispatch_post: HttpRequest
    ) -> None:
        with pytest.raises(LookupError):
            _fail_a_visit(dispatch_post)
        assert dispatch_post.META["CSRF_COOKIE_NEEDS_UPDATE"] is True
