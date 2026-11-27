from collections.abc import Callable, Generator
from contextlib import AbstractContextManager, contextmanager
from pathlib import Path
from unittest import mock

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group, Permission
from django.core.exceptions import PermissionDenied
from django.core.handlers.base import BaseHandler
from django.db import connection, connections, transaction
from django.db.utils import ConnectionHandler
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings

from next.pages.loaders import load_python_module_memo
from next.partial.headers import BOUNDARY
from next.requests import RequestKind
from next.transactions import (
    AtomicScope,
    lifecycle as transactions_lifecycle,
    request_atomic,
)
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    routed,
    unified_view,
    write_page,
)
from tests.support.access import COMPONENTS
from tests.support.rules import KINDS_SEEN
from tests.support.views import ZONED_BODY, write_dynamic_page


User = get_user_model()


_RULE = {"TRANSACTIONS": {"RULE": "tests.support.rules.record_kind"}}


def _writing_page(
    directory: Path, *, declaration: str = "", ending: str = "return '<p>written</p>'"
) -> Path:
    """Write a `page.py` whose `render()` writes a row and records its transaction."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text(
        "from django.contrib.auth.models import Group\n"
        "from django.db import connection, transaction\n"
        "from django.http import HttpResponse\n"
        "from next.pages import page\n"
        f"{declaration}\n"
        "EVENTS = []\n\n\n"
        "def render(request):\n"
        "    transaction.on_commit(lambda: EVENTS.append('commit'))\n"
        "    EVENTS.append(f'atomic={connection.in_atomic_block}')\n"
        "    Group.objects.create(name='from-page')\n"
        f"    {ending}\n"
    )
    return page_file


def _request(method: str) -> HttpRequest:
    request = build_page_request()
    request.method = method
    return request


def _events(page_file: Path) -> list[str]:
    return load_python_module_memo(page_file).EVENTS


def _recorder(events: list[str]) -> Callable[..., object]:
    @contextmanager
    def atomic(using: str | None = None) -> Generator[None]:
        events.append(f"enter {using}")
        try:
            yield
        finally:
            events.append(f"exit {using}")

    return atomic


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("fresh_transaction_declarations")
class TestPageTransaction:
    """A page view opens the transaction the method rule or `page.transaction` asks."""

    def test_a_get_runs_outside_a_transaction(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path)
        unified_view(page_instance, page_file)(_request("GET"))
        assert _events(page_file) == ["commit", "atomic=False"]
        assert Group.objects.filter(name="from-page").exists()

    @pytest.mark.parametrize("atomic_requests", [True, False])
    def test_a_post_runs_in_one_transaction_with_the_hook_after_commit(
        self, page_instance, tmp_path, *, atomic_requests: bool
    ) -> None:
        page_file = _writing_page(tmp_path)
        view = unified_view(page_instance, page_file)
        with mock.patch.dict(
            connections.settings["default"], {"ATOMIC_REQUESTS": atomic_requests}
        ):
            BaseHandler().make_view_atomic(view)(_request("POST"))
        assert _events(page_file) == ["atomic=True", "commit"]
        assert Group.objects.filter(name="from-page").exists()

    def test_an_exception_rolls_back(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path, ending="raise ValueError('page failed')")
        view = unified_view(page_instance, page_file)
        with pytest.raises(ValueError, match="page failed"):
            view(_request("POST"))
        assert _events(page_file) == ["atomic=True"]
        assert not Group.objects.filter(name="from-page").exists()

    @pytest.mark.parametrize("status", [404, 500])
    def test_an_error_response_commits(
        self, page_instance, tmp_path, status: int
    ) -> None:
        page_file = _writing_page(
            tmp_path, ending=f"return HttpResponse(status={status})"
        )
        response = unified_view(page_instance, page_file)(_request("POST"))
        assert response.status_code == status
        assert _events(page_file) == ["atomic=True", "commit"]
        assert Group.objects.filter(name="from-page").exists()

    def test_page_transaction_on_wraps_a_get(self, page_instance, tmp_path) -> None:
        page_file = _writing_page(tmp_path, declaration="page.transaction(atomic=True)")
        unified_view(page_instance, page_file)(_request("GET"))
        assert _events(page_file) == ["atomic=True", "commit"]

    def test_page_transaction_off_leaves_a_post_bare(
        self, page_instance, tmp_path
    ) -> None:
        page_file = _writing_page(
            tmp_path, declaration="page.transaction(atomic=False)"
        )
        unified_view(page_instance, page_file)(_request("POST"))
        assert _events(page_file) == ["commit", "atomic=False"]


class _Refused:
    """A signed-in user refused every permission, noting the transaction asked in."""

    is_authenticated = True

    def __init__(self) -> None:
        self.asked: list[bool] = []

    def has_perm(self, perm: str, obj: object = None) -> bool:
        self.asked.append(connection.in_atomic_block)
        return False


_GUARDED = "page.transaction(atomic=True)\npage.permission_required('auth.view_group')"
_PARTIAL = {"X-Next-Request": "1"}
_ROUTE_HEADERS = {
    "page": {},
    "zone": {**_PARTIAL, "X-Next-Zone": "z"},
    "navigation": {**_PARTIAL, "X-Next-Navigate": "1", "Sec-Fetch-Site": "same-origin"},
    "boundary": {**_PARTIAL, BOUNDARY: "token"},
}


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("fresh_transaction_declarations", "router_on")
class TestRefusalBeforeTransaction:
    """A page refuses a request before it opens the `page.transaction` transaction."""

    def _opened(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, ...]]:
        opened: list[tuple[str, ...]] = []

        def recording(scope: AtomicScope) -> AbstractContextManager[object]:
            if scope.aliases:
                opened.append(scope.aliases)
            return request_atomic(scope)

        monkeypatch.setattr(transactions_lifecycle, "request_atomic", recording)
        return opened

    @pytest.fixture()
    def routed_request(self, rf) -> Callable[[str, object], HttpRequest]:
        """Return a builder of a GET on `route` made by `user`."""

        def build(route: str, user: object) -> HttpRequest:
            request = rf.get("/", headers=_ROUTE_HEADERS[route])
            request.user = user
            return request

        return build

    @pytest.mark.parametrize("route", list(_ROUTE_HEADERS))
    @pytest.mark.parametrize("dynamic", [True, False], ids=["render", "static"])
    def test_a_refused_request_opens_no_transaction(
        self,
        page_instance,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        django_assert_num_queries,
        routed_request,
        route: str,
        *,
        dynamic: bool,
    ) -> None:
        page_file = (
            _writing_page(tmp_path, declaration=_GUARDED)
            if dynamic
            else write_page(
                tmp_path,
                "",
                f"from next.pages import page\n{_GUARDED}\n",
                body=ZONED_BODY,
            )
        )
        view = unified_view(page_instance, page_file)
        user = _Refused()
        opened = self._opened(monkeypatch)
        with django_assert_num_queries(0):
            if route == "boundary":
                assert view(routed_request(route, user)).status_code == 403
            else:
                with pytest.raises(PermissionDenied):
                    view(routed_request(route, user))
        assert user.asked == [False]
        assert opened == []
        assert not Group.objects.filter(name="from-page").exists()

    def test_an_anonymous_request_is_sent_to_log_in_outside_it(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch, routed_request
    ) -> None:
        page_file = _writing_page(tmp_path, declaration=_GUARDED)
        opened = self._opened(monkeypatch)
        response = unified_view(page_instance, page_file)(
            routed_request("page", AnonymousUser())
        )
        assert response.status_code == 302
        assert opened == []

    def test_an_admitted_request_runs_in_the_transaction(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch, routed_request
    ) -> None:
        opened = self._opened(monkeypatch)
        page_file = _writing_page(tmp_path, declaration=_GUARDED)
        user = User.objects.create_user("ada")
        user.user_permissions.add(Permission.objects.get(codename="view_group"))
        response = unified_view(page_instance, page_file)(
            routed_request("page", User.objects.get(pk=user.pk))
        )
        assert response.status_code == 200
        assert _events(page_file) == ["atomic=True", "commit"]
        assert Group.objects.filter(name="from-page").exists()
        assert opened == [("default",)]


class TestPageTransactionWiring:
    """The view stays outside `ATOMIC_REQUESTS` and names its kind to the rule."""

    def test_a_get_opens_nothing_and_touches_no_connection(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)
        opened: list[str] = []
        touched: list[str] = []

        def touch(handler: ConnectionHandler, alias: str) -> None:
            touched.append(alias)

        monkeypatch.setattr(transaction, "atomic", _recorder(opened))
        monkeypatch.setattr(ConnectionHandler, "__getitem__", touch)
        response = view(_request("GET"))
        assert response.status_code == 200
        assert opened == []
        assert touched == []

    @pytest.mark.parametrize("atomic_requests", [True, False])
    def test_atomic_requests_adds_no_second_transaction(
        self,
        page_instance,
        tmp_path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        atomic_requests: bool,
    ) -> None:
        page_file = build_nested_page(tmp_path)
        view = unified_view(page_instance, page_file)
        opened: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(opened))
        with mock.patch.dict(
            connections.settings["default"], {"ATOMIC_REQUESTS": atomic_requests}
        ):
            wrapped = BaseHandler().make_view_atomic(view)
        wrapped(_request("POST"))
        assert wrapped is view
        assert opened == ["enter default", "exit default"]

    @pytest.mark.parametrize("dynamic", [True, False])
    def test_atomic_requests_on_two_aliases_leaves_the_view_unwrapped(
        self, page_instance, tmp_path, monkeypatch: pytest.MonkeyPatch, *, dynamic: bool
    ) -> None:
        page_file = (
            write_dynamic_page(tmp_path / "dynamic")
            if dynamic
            else build_nested_page(tmp_path)
        )
        declared = {"ATOMIC_REQUESTS": True}
        monkeypatch.setattr(
            connections, "settings", {"default": declared, "replica": declared}
        )
        view = unified_view(page_instance, page_file)
        assert BaseHandler().make_view_atomic(view) is view

    def test_the_rule_hears_the_page_and_the_zone_kind(
        self, page_instance, tmp_path
    ) -> None:
        page_file = build_nested_page(tmp_path, body=ZONED_BODY)
        view = unified_view(page_instance, page_file)
        rule = _RULE
        with override_settings(NEXT_FRAMEWORK=rule):
            view(build_page_request())
            view(build_zone_request("z"))
        assert KINDS_SEEN == [RequestKind.PAGE, RequestKind.ZONE]

    def test_the_rule_hears_the_page_kind_on_a_dynamic_page(
        self, page_instance, tmp_path
    ) -> None:
        page_file = write_dynamic_page(tmp_path / "dynamic")
        view = unified_view(page_instance, page_file)
        rule = _RULE
        with override_settings(NEXT_FRAMEWORK=rule):
            view(build_page_request())
        assert KINDS_SEEN == [RequestKind.PAGE]


_LAZY = 'page.transaction(atomic="writes")'


def _lazy_page(directory: Path, *, asynchronous: bool, fail: bool = False) -> Path:
    """Write a `"writes"` page that notes its transaction around one write."""
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    ending = "raise ValueError('page failed')" if fail else "return '<p>written</p>'"
    render = (
        f"async def render(request):\n    await sync_to_async(_write)()\n    {ending}\n"
        if asynchronous
        else f"def render(request):\n    _write()\n    {ending}\n"
    )
    page_file.write_text(
        "from asgiref.sync import sync_to_async\n"
        "from django.contrib.auth.models import Group\n"
        "from django.db import connection, transaction\n"
        "from next.pages import page\n"
        f"{_LAZY}\n"
        "EVENTS = []\n\n\n"
        "def _write():\n"
        "    EVENTS.append(f'before={connection.in_atomic_block}')\n"
        "    transaction.on_commit(lambda: EVENTS.append('early'))\n"
        "    Group.objects.create(name='from-page')\n"
        "    EVENTS.append(f'after={connection.in_atomic_block}')\n"
        "    transaction.on_commit(lambda: EVENTS.append('commit'))\n\n\n"
        f"{render}"
    )
    return page_file


@pytest.mark.django_db(transaction=True)
@pytest.mark.usefixtures("fresh_transaction_declarations")
@pytest.mark.parametrize("asynchronous", [False, True], ids=["sync", "async"])
class TestLazyPageTransaction:
    """A `"writes"` page opens its transaction at the first write or commit hook."""

    def test_the_transaction_opens_at_the_first_hook_or_write(
        self, page_instance, tmp_path, *, asynchronous: bool
    ) -> None:
        page_file = _lazy_page(tmp_path, asynchronous=asynchronous)
        response = unified_view(page_instance, page_file)(_request("GET"))
        assert response.status_code == 200
        assert _events(page_file) == ["before=False", "after=True", "early", "commit"]
        assert Group.objects.filter(name="from-page").exists()
        assert not connection.in_atomic_block
        assert connection.execute_wrappers == []

    def test_a_failure_after_the_write_rolls_the_page_back(
        self, page_instance, tmp_path, *, asynchronous: bool
    ) -> None:
        page_file = _lazy_page(tmp_path, asynchronous=asynchronous, fail=True)
        with pytest.raises(ValueError, match="page failed"):
            unified_view(page_instance, page_file)(_request("POST"))
        assert _events(page_file) == ["before=False", "after=True"]
        assert not Group.objects.exists()
        assert connection.execute_wrappers == []


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_a_query_free_writes_page_needs_no_database(
    page_instance, tmp_path, method: str
) -> None:
    page_file = build_nested_page(tmp_path)
    view = unified_view(page_instance, page_file)
    scope = {"TRANSACTIONS": {"SAFE": "writes", "UNSAFE": "writes"}}
    with override_settings(NEXT_FRAMEWORK=scope):
        response = view(_request(method))
    assert response.status_code == 200


def test_an_eager_post_needs_the_database(page_instance, tmp_path) -> None:
    view = unified_view(page_instance, build_nested_page(tmp_path))
    with pytest.raises(RuntimeError, match="Database access not allowed"):
        view(_request("POST"))


_BOUNDARY_WRITE_SYNC = """
from django.contrib.auth.models import Group
from next.pages import context, page

page.transaction(atomic="writes")


@context("value")
def value():
    Group.objects.create(name="boundary")
    raise ValueError("context")
"""

_BOUNDARY_WRITE_ASYNC = """
from django.contrib.auth.models import Group
from next.pages import context, page

page.transaction(atomic="writes")


@context("value")
async def value():
    await Group.objects.acreate(name="boundary")
    raise ValueError("context")


async def render():
    return "{{ value }}"
"""


@pytest.fixture()
def guarded_pages(tmp_path: Path) -> Path:
    """Return a `pages` root whose `sec` section carries an `error.djx` boundary."""
    pages = tmp_path / "pages"
    (pages / "sec").mkdir(parents=True)
    (pages / "layout.djx").write_text("<html>{% template %}</html>")
    (pages / "sec" / "error.djx").write_text("<p>sec {{ error.kind }}</p>")
    return pages


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("atomic", [True, "writes", False])
@pytest.mark.parametrize(
    "source", [_BOUNDARY_WRITE_SYNC, _BOUNDARY_WRITE_ASYNC], ids=["sync", "async"]
)
class TestBoundaryInsideALazyPage:
    """A page boundary undoes its writes inside a `"writes"` page on every `ATOMIC`."""

    def test_the_write_of_a_contained_failure_rolls_back(
        self, guarded_pages: Path, source: str, atomic: object
    ) -> None:
        write_page(guarded_pages / "sec" / "leaf", source=source, body="{{ value }}")
        boundaries = {"ATOMIC": atomic}
        with routed(
            guarded_pages, COMPONENT_BACKENDS=COMPONENTS, BOUNDARIES=boundaries
        ):
            response = Client().get("/sec/leaf/")
        assert response.status_code == 500
        assert b"sec error" in response.content
        assert not Group.objects.exists()

    def test_the_same_holds_under_asgi(
        self, guarded_pages: Path, source: str, atomic: object
    ) -> None:
        write_page(guarded_pages / "sec" / "leaf", source=source, body="{{ value }}")
        boundaries = {"ATOMIC": atomic}
        with routed(
            guarded_pages, COMPONENT_BACKENDS=COMPONENTS, BOUNDARIES=boundaries
        ):
            response = async_to_sync(AsyncClient().get)("/sec/leaf/")
        assert response.status_code == 500
        assert not Group.objects.exists()
