from pathlib import Path

import pytest
from django.http import HttpResponse
from django.test import RequestFactory
from django.urls import clear_script_prefix, set_script_prefix

from next.deps import REQUEST_DEP_CACHE_ATTR, get_request_dep_cache
from next.pages.loaders import _load_python_module
from next.pages.visits import visit_request
from next.testing import NextClient, envelope_of


_SITE_PAGES = Path(__file__).resolve().parent.parent / "site_pages"
_SHAPED_PAGE = _SITE_PAGES / "shaped" / "page.py"
_ACTION_PATH = "/_next/form/uid/"


@pytest.fixture()
def dispatch_post():
    """Return a POST to the dispatch endpoint, the shape an origin is posted with."""
    return RequestFactory().post(
        _ACTION_PATH, data={"note": "hi"}, QUERY_STRING="stale=1"
    )


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
        assert get_request_dep_cache(visit) is None
        assert get_request_dep_cache(dispatch_post) is cache


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
        set_script_prefix("/app/")
        try:
            visit = visit_request(dispatch_post, "/app/notes/")
        finally:
            clear_script_prefix()
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
            shaped_client, "/shaped/?token=open", partial=True, zones="shaped-zone"
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
