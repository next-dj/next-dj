import re
import threading
from collections.abc import Iterator

import pytest
from asgiref.sync import async_to_sync
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIRequest
from django.http import HttpRequest, HttpResponseBase
from django.test import RequestFactory, override_settings

from next.conf.sentinels import UNSET
from next.pages.loaders import load_page_module
from next.requests import (
    PrefetchDepth,
    Purpose,
    RequestInfo,
    RequestKind,
    RequestNext,
    depth_of,
    held_next,
    mark_purpose,
    next_of,
    purpose_of,
)
from next.testing import AsyncNextClient, NextClient
from tests.support import isolated_form_registries, routed
from tests.support.cases.requests import KindCase
from tests.support.requests import FORMS_PAGE, SITE_PAGES, URLCONF


def test_every_kind_has_one_spelling_on_the_wire() -> None:
    assert [kind.value for kind in RequestKind] == [
        "page",
        "action",
        "zone",
        "boundary",
        "navigate",
    ]


def test_a_prefetch_asks_for_the_shell_or_the_full_page() -> None:
    assert [depth.value for depth in PrefetchDepth] == ["shell", "full"]


_MEMOS = ("nonce", "origin_match", "site_declared", "site_indexable")
_FLAGS = (
    "csrf_deferred",
    "shared_render",
    "personal_render",
    "bound_render",
    "cookie_vary",
)

_RESET = {"dep_cache": None, "origin_match": UNSET, "boundary_token": None}


class _Shadowed:
    """A request whose `next` reads as absent while its dict already holds one."""

    @property
    def next(self) -> None:
        raise AttributeError


class TestNamespace:
    """A fresh namespace stores every slot and shows only its public attributes."""

    def test_every_slot_starts_stored(self) -> None:
        space = RequestNext()
        for name in RequestNext.__slots__:
            held = getattr(space, name)
            if name == "info":
                assert held == RequestInfo(), name
            elif name in _MEMOS:
                assert held is UNSET, name
            elif name in _FLAGS:
                assert held is False, name
            else:
                assert held is None, name

    def test_no_page_view_means_a_document_without_a_depth(self) -> None:
        space = RequestNext()
        assert (space.purpose, space.depth) == (Purpose.DOCUMENT, None)

    def test_the_info_names_the_purpose_and_the_depth(self) -> None:
        space = RequestNext()
        space.info = RequestInfo(Purpose.PREFETCH, PrefetchDepth.FULL)
        assert (space.purpose, space.depth) == (Purpose.PREFETCH, PrefetchDepth.FULL)

    def test_the_repr_shows_the_public_attributes_only(self) -> None:
        space = RequestNext()
        space.info = RequestInfo(Purpose.NAVIGATE)
        space.intent = "held"
        assert repr(space) == (
            "<RequestNext purpose=<Purpose.NAVIGATE: 'navigate'> depth=None>"
        )


class TestFork:
    """The namespace of a visit shares the boundary state and resets three slots."""

    def test_each_slot_follows_its_rule(self) -> None:
        space = RequestNext()
        held = {name: object() for name in RequestNext.__slots__}
        for name, value in held.items():
            setattr(space, name, value)
        visit = space.fork()
        assert type(visit) is RequestNext
        assert visit is not space
        for name, value in held.items():
            expected = _RESET.get(name, value)
            assert getattr(visit, name) is expected, name
        assert visit.defer is space.defer

    def test_a_fork_of_a_fork_points_at_the_first_namespace(self) -> None:
        space = RequestNext()
        assert space.fork().root is space
        assert space.fork().fork().root is space

    def test_the_source_keeps_its_slots(self) -> None:
        space = RequestNext()
        space.dep_cache = cache = object()
        space.fork().dep_cache = object()
        assert space.dep_cache is cache


class TestAdopt:
    """A visit hands back the flags and nonce that keep the live response private."""

    def test_a_set_flag_and_a_minted_nonce_come_back(self) -> None:
        space, visit = RequestNext(), RequestNext()
        visit.personal_render = visit.bound_render = visit.cookie_vary = True
        visit.nonce = "visit-nonce"
        space.adopt(visit)
        assert (space.personal_render, space.bound_render, space.cookie_vary) == (
            True,
            True,
            True,
        )
        assert space.nonce == "visit-nonce"

    def test_an_unset_flag_or_nonce_keeps_the_parent(self) -> None:
        space, visit = RequestNext(), RequestNext()
        space.personal_render = space.bound_render = space.cookie_vary = True
        space.nonce = "kept"
        space.adopt(visit)
        assert (space.personal_render, space.bound_render, space.cookie_vary) == (
            True,
            True,
            True,
        )
        assert space.nonce == "kept"

    def test_a_none_nonce_is_a_memo_and_comes_back(self) -> None:
        space, visit = RequestNext(), RequestNext()
        visit.nonce = None
        space.adopt(visit)
        assert space.nonce is None

    def test_nothing_else_comes_back(self) -> None:
        space, visit = RequestNext(), RequestNext()
        visit.shared_render = visit.csrf_deferred = True
        visit.intent = object()
        space.adopt(visit)
        assert (space.shared_render, space.csrf_deferred, space.intent) == (
            False,
            False,
            None,
        )


class TestNextOf:
    """The namespace is made once per request, whichever thread asks first."""

    def test_the_namespace_is_made_once(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        assert next_of(request) is next_of(request) is request.next
        assert type(request.next) is RequestNext

    def test_a_made_namespace_reads_as_a_document(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        assert next_of(request).purpose is Purpose.DOCUMENT
        assert purpose_of(request) is Purpose.DOCUMENT
        assert depth_of(request) is None

    def test_a_namespace_another_writer_stored_meanwhile_is_kept(self) -> None:
        request = _Shadowed()
        vars(request)["next"] = held = RequestNext()
        assert next_of(request) is held

    def test_two_threads_get_one_namespace(self, rf: RequestFactory) -> None:
        for _ in range(50):
            request = rf.get("/")
            start = threading.Barrier(2)
            got: list[RequestNext] = []

            def take(request: object = request, start=start, got=got) -> None:
                start.wait()
                got.append(next_of(request))

            threads = [threading.Thread(target=take) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            assert got[0] is got[1] is request.next


class TestHeldNext:
    """A reader sees the namespace a writer made and never makes one itself."""

    def test_a_request_without_one_holds_none(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        assert held_next(request) is None
        assert "next" not in vars(request)

    def test_the_made_namespace_is_held(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        made = next_of(request)
        assert held_next(request) is made

    def test_no_request_holds_none(self) -> None:
        assert held_next(None) is None


class TestPurpose:
    """A page view marks the purpose and depth its request names."""

    def test_an_info_has_no_depth_by_default(self) -> None:
        assert RequestInfo().depth is None

    @pytest.mark.parametrize(
        ("purpose", "depth"),
        [
            (Purpose.NAVIGATE, PrefetchDepth.FULL),
            (Purpose.DOCUMENT, PrefetchDepth.SHELL),
            (Purpose.PREFETCH, None),
        ],
    )
    def test_an_impossible_pair_is_refused(
        self, purpose: Purpose, depth: PrefetchDepth | None, rf: RequestFactory
    ) -> None:
        with pytest.raises(KeyError, match=re.escape(repr((purpose, depth)))):
            mark_purpose(rf.get("/"), purpose, depth)

    def test_a_purpose_without_a_depth(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        mark_purpose(request, Purpose.NAVIGATE)
        assert purpose_of(request) is Purpose.NAVIGATE
        assert depth_of(request) is None
        assert request.next.info == RequestInfo(Purpose.NAVIGATE)
        assert request.next.purpose is Purpose.NAVIGATE

    @pytest.mark.parametrize("depth", list(PrefetchDepth))
    def test_a_marked_depth(self, depth: PrefetchDepth, rf: RequestFactory) -> None:
        request = rf.get("/")
        mark_purpose(request, Purpose.PREFETCH, depth)
        assert purpose_of(request) is Purpose.PREFETCH
        assert depth_of(request) is depth
        assert request.next.depth is depth

    def test_the_info_is_shared(self, rf: RequestFactory) -> None:
        first, second = rf.get("/"), rf.get("/")
        mark_purpose(first, Purpose.PREFETCH, PrefetchDepth.FULL)
        mark_purpose(second, Purpose.PREFETCH, PrefetchDepth.FULL)
        assert first.next is not second.next
        assert first.next.info is second.next.info

    def test_a_later_mark_keeps_the_boundary_state(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        root = next_of(request)
        root.defer = held = object()
        mark_purpose(request, Purpose.NAVIGATE)
        assert request.next is root
        assert (root.purpose, root.depth, root.defer) == (Purpose.NAVIGATE, None, held)

    def test_an_untouched_request_reads_as_a_document(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        assert (purpose_of(request), depth_of(request)) == (Purpose.DOCUMENT, None)
        assert "next" not in vars(request)

    def test_an_object_that_is_no_request_has_no_depth(self) -> None:
        assert depth_of(object()) is None


class TestForeignRequestNext:
    """A value next.dj did not set under `request.next` is refused by name and type."""

    _MESSAGE = re.escape(
        "request.next holds a str, but next.dj owns request.next and refuses any "
        "other value there."
    )

    def test_making_the_namespace_refuses_it(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        request.next = "/home"
        with pytest.raises(ImproperlyConfigured, match=self._MESSAGE):
            next_of(request)
        assert request.next == "/home"

    def test_the_message_names_the_owner_and_the_fix(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        request.next = "/home"
        with pytest.raises(ImproperlyConfigured, match=self._MESSAGE) as caught:
            next_of(request)
        assert "Keep project state under another attribute name." in str(caught.value)

    def test_marking_the_purpose_refuses_it(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        request.next = "/home"
        with pytest.raises(ImproperlyConfigured, match=self._MESSAGE):
            mark_purpose(request, Purpose.NAVIGATE)

    @pytest.mark.parametrize("reader", [purpose_of, depth_of, held_next])
    def test_reading_it_refuses_it(self, reader, rf: RequestFactory) -> None:
        request = rf.get("/")
        request.next = "/home"
        with pytest.raises(ImproperlyConfigured, match=self._MESSAGE):
            reader(request)

    def test_a_none_is_foreign_too(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        request.next = None
        with pytest.raises(ImproperlyConfigured, match="holds a NoneType"):
            next_of(request)

    @pytest.mark.parametrize("reader", [purpose_of, depth_of, held_next])
    def test_reading_a_none_refuses_it(self, reader, rf: RequestFactory) -> None:
        request = rf.get("/")
        request.next = None
        with pytest.raises(ImproperlyConfigured, match="holds a NoneType"):
            reader(request)

    def test_a_subclass_is_foreign(self, rf: RequestFactory) -> None:
        class Lookalike(RequestNext):
            __slots__ = ()

        request = rf.get("/")
        request.next = Lookalike()
        with pytest.raises(ImproperlyConfigured, match="holds a Lookalike"):
            next_of(request)

    def test_a_foreign_value_another_writer_stored_meanwhile_is_refused(self) -> None:
        request = _Shadowed()
        vars(request)["next"] = "/home"
        with pytest.raises(ImproperlyConfigured, match=self._MESSAGE):
            next_of(request)


@pytest.fixture()
def kinds_client() -> Iterator[NextClient]:
    with (
        isolated_form_registries(),
        routed(SITE_PAGES, urlconf=URLCONF, ROUTER={"ENABLED": True}),
    ):
        load_page_module(FORMS_PAGE)
        yield NextClient(enforce_csrf_checks=False)


def _settled(response: HttpResponseBase) -> HttpRequest:
    request = response.wsgi_request
    assert isinstance(request, HttpRequest)
    return request


def _served(response: HttpResponseBase) -> ASGIRequest:
    request = response.asgi_request
    assert isinstance(request, ASGIRequest)
    return request


_REQUIRED = b"This field is required."
_CREDENTIALS = {"username": "visitor", "password": "secret-visitor-password"}


def _document(client: NextClient) -> HttpRequest:
    return _settled(client.get("/board_forms/"))


def _navigate(client: NextClient) -> HttpRequest:
    return _settled(client.navigate("/board_forms/", layouts="").response)


def _shell(client: NextClient) -> HttpRequest:
    navigation = client.navigate("/board_forms/", layouts="", prefetch=True)
    return _settled(navigation.response)


def _zone(client: NextClient) -> HttpRequest:
    response = client.get_zones("/zoned/", "alpha")
    assert b"alpha hi" in response.content
    return _settled(response)


def _rerender(client: NextClient) -> HttpRequest:
    response = client.post_action(
        "rename_board_form", {"title": ""}, origin="/board_forms/"
    )
    assert _REQUIRED in response.content
    return _settled(response)


def _action(client: NextClient) -> HttpRequest:
    response = client.post_action(
        "rename_board_form", {"title": "Roadmap"}, origin="/board_forms/"
    )
    assert response.status_code == 200
    assert _REQUIRED not in response.content
    return _settled(response)


def _plain(client: NextClient) -> HttpRequest:
    response = client.get("/plain/")
    assert b"test page" in response.content
    return _settled(response)


def _login(client: NextClient) -> HttpRequest:
    get_user_model().objects.create_user(**_CREDENTIALS)
    response = client.post("/login/", _CREDENTIALS)
    assert response.status_code == 302
    return _settled(response)


def _get(client: NextClient) -> HttpRequest:
    return _settled(client.get("/control/"))


def _post(client: NextClient) -> HttpRequest:
    return _settled(client.post("/control/", {"field": "value"}))


def _login_without_router(client: NextClient) -> HttpRequest:
    with routed(SITE_PAGES, urlconf=URLCONF):
        response = client.post("/login/", _CREDENTIALS)
    return _settled(response)


KINDS: tuple[KindCase, ...] = (
    KindCase("document", _document, _get),
    KindCase("navigate", _navigate, _get, Purpose.NAVIGATE),
    KindCase("shell-prefetch", _shell, _get, Purpose.PREFETCH, PrefetchDepth.SHELL),
    KindCase("zone", _zone, _get),
    KindCase("action-rerender", _rerender, _post),
    KindCase("plain-view", _plain, _get),
    KindCase("login-view", _login, _login_without_router),
)
ACTION = KindCase("action", _action, _post)


@pytest.mark.django_db()
class TestRequestKinds:
    """A real request of each key kind keeps what next.dj stores on `request.next`."""

    @pytest.mark.parametrize("kind", KINDS, ids=lambda kind: kind.id)
    def test_only_the_namespace_is_added(self, kind: KindCase, kinds_client) -> None:
        request = kind.drive(kinds_client)
        control = kind.control(kinds_client)
        assert type(request) is type(control)
        assert set(vars(request)) - set(vars(control)) <= {"next"}

    def test_an_asgi_document_adds_only_the_namespace(self, kinds_client) -> None:
        client = AsyncNextClient()
        request = _served(async_to_sync(client.get)("/board_forms/"))
        control = _served(async_to_sync(client.get)("/control/"))
        assert set(vars(request)) - set(vars(control)) <= {"next"}
        assert request.next.purpose is Purpose.DOCUMENT

    @pytest.mark.parametrize("kind", [*KINDS, ACTION], ids=lambda kind: kind.id)
    def test_the_namespace_names_the_purpose(
        self, kind: KindCase, kinds_client
    ) -> None:
        request = kind.drive(kinds_client)
        assert (request.next.purpose, request.next.depth) == (kind.purpose, kind.depth)


@pytest.mark.django_db()
class TestLoginNamespace:
    """A login reaches `request.next` only through the router that clears on it."""

    def test_a_foreign_value_a_middleware_set_is_refused(self, kinds_client) -> None:
        get_user_model().objects.create_user(**_CREDENTIALS)
        middleware = [*settings.MIDDLEWARE, "tests.support.requests.foreign_next"]
        with (
            override_settings(MIDDLEWARE=middleware),
            pytest.raises(ImproperlyConfigured, match="holds a str"),
        ):
            kinds_client.post("/login/", _CREDENTIALS)

    def test_the_router_off_makes_no_namespace(self) -> None:
        get_user_model().objects.create_user(**_CREDENTIALS)
        with routed(SITE_PAGES, urlconf=URLCONF):
            response = NextClient().post("/login/", _CREDENTIALS)
        assert response.status_code == 302
        assert "next" not in vars(_settled(response))
