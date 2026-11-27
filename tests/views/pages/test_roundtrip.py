import hashlib
import json
import logging
import re
from collections.abc import Generator
from contextlib import contextmanager
from html import unescape
from pathlib import Path
from urllib.parse import urlencode

import pytest
from django.contrib.auth.models import Permission, User
from django.http import HttpResponse
from django.template import TemplateSyntaxError
from django.test import Client, override_settings

from next.components.registry import DeferredComponents
from next.defer import BoundaryPropsError
from next.defer.manager import defer_manager
from next.defer.tokens import BoundaryClaims
from next.signals import boundary_token_rejected
from next.testing import (
    NextClient,
    capture_signals,
    freeze_token_clock,
    issue_boundary_token,
    reset_failure_logs,
)
from tests.support import hosts as host, routed, write_page
from tests.support.access import COMPONENTS
from tests.support.components import write_component


pytestmark = pytest.mark.django_db()


_CARD = """
from next.components import component
from tests.support import hosts as host

component.defer("visible", props=("sku", "n"))


@component.context("title")
def title(sku: str, n: int) -> str:
    host.RUNS.append((sku, n))
    return f"card {sku} x{n * 2}"
"""

_LOCKED = """
from next.components import component

component.defer("load", props=("sku",))
component.permission_required("auth.change_user")
"""

_ROW = """
from django.contrib.auth.models import User

from next.components import component

component.defer("manual", props=("target",))


@component.context("label")
def label(target: User) -> str:
    return f"row {target.username}"
"""

_OWNED = """
from django.contrib.auth.models import User

from next.components import component

component.defer("load", props=("target",))
component.permission_required("auth.change_user", obj="target")


@component.context("owner")
def owner(target: User) -> str:
    return target.username
"""

_BOOM = """
from next.components import component

component.defer("load")


@component.context("value")
def value() -> str:
    raise ValueError("down")
"""

_FLAKY = """
from next.components import component

component.props("q")


@component.context("value")
def value(q: str) -> str:
    raise ValueError(q)
"""

_MISSING = """
from django.http import Http404

from next.components import component

component.defer("load")


@component.context("value")
def value() -> str:
    raise Http404
"""

_RENDERED = """
from next import page

page.public()


def render() -> str:
    return "<p>rendered</p>"
"""

_ROWS_PAGE = """
from django.contrib.auth.models import User

from next import context, page
from next.urls import DUrl

page.public()


@context("target")
def target(id: DUrl[int]) -> User:
    return User.objects.get(pk=id)
"""

_PUBLIC = "from next import page\n\npage.public()\n"
_PRIVATE = "from next import page\n\npage.login_required()\n"
_WRAPPER = re.compile(
    r'<div id="(?P<id>b-[0-9a-f]{8}-[0-9]+)" data-next-boundary="(?P=id)" '
    r'data-next-boundary-key="[0-9a-f]{8}" data-next-defer="(?P<mode>\w+)" '
    r'data-next-token="(?P<token>[^"]+)"(?P<rest>[^>]*)>'
)


_PEEK = """
from next.components import component
from tests.support import hosts as host

component.defer("load", props=("uid",))


@component.context("secret")
def secret(uid: int) -> str:
    host.RUNS.append(("peek", uid))
    return f"secret {uid}"
"""

_GATED = """
from django.contrib.auth.models import User

from next.components import component

component.defer("manual", props=("target",))
component.permission_required("auth.view_user")


@component.context("label")
def label(target: User) -> str:
    return f"gated {target.username}"
"""

_STAFF_GATE = """
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from next import context, page

page.public()


@context("gate", guard=True)
def gate(request: HttpRequest) -> str:
    if not request.user.is_staff:
        raise PermissionDenied
    return "ok"
"""

_MISSING_GATE = _STAFF_GATE.replace(
    "from django.core.exceptions import PermissionDenied",
    "from django.http import Http404",
).replace("raise PermissionDenied", "raise Http404")

_ASYNC_GATE = """
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from next import context, page

page.public()


@context("gate", guard=True)
async def gate(request: HttpRequest) -> str:
    user = await request.auser()
    if not user.is_staff:
        raise PermissionDenied
    return "ok"
"""

_FLAGGED_GATE = """
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest

from next import context, page

page.public()


@context("gate", guard=True)
def gate(request: HttpRequest) -> str:
    refusal = PermissionDenied
    if not request.user.is_staff:
        raise refusal
    return "ok"
"""

_INHERITED_GATE = _STAFF_GATE.replace(
    "page.public()", "page.public(inherit=True)"
).replace(
    '@context("gate", guard=True)', '@context("gate", guard=True, inherit_context=True)'
)

_SEARCH = """
from next.components import component

component.defer("visible", props=("q",))


@component.context("found")
def found(q: str) -> str:
    return f"found {len(q)}"
"""

_SEARCH_PAGE = """
from django.http import HttpRequest

from next import context, page

page.public()


@context("q")
def q(request: HttpRequest) -> object:
    asked = request.GET.get("q", "")
    return {"q": asked} if request.GET.get("as") == "mapping" else asked
"""

_RATIO = """
from decimal import Decimal

from next.components import component

component.defer("visible", props=("x", "d"))


@component.context("shown")
def shown(x: float, d: Decimal) -> str:
    return f"ratio {x} {d}"
"""

_RATIO_PAGE = """
from decimal import Decimal

from django.http import HttpRequest

from next import context, page

page.public()


@context("x")
def x(request: HttpRequest) -> float:
    return float(request.GET.get("x", "1"))


@context("d")
def d(request: HttpRequest) -> Decimal:
    return Decimal(request.GET.get("d", "1"))
"""

_STAFF_PAGE = 'from next import page\n\npage.permission_required("auth.change_user")\n'

_ECHO = "<p>{{ request.get_full_path }}|{{ request.META.QUERY_STRING }}</p>"


@pytest.fixture()
def pages(tmp_path: Path, fresh_deferred_components: DeferredComponents) -> Path:
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text("<html><body>{% template %}</body></html>")
    write_component(
        root,
        "card",
        module=_CARD,
        body="<p>{{ title }}</p>",
        loading="<i>loading {{ sku }}</i>",
        error="<b data-next-retry>failed</b>",
    )
    write_component(
        root,
        "locked",
        module=_LOCKED,
        body="<p>locked {{ sku }}</p>",
        loading="<i>loading</i>",
    )
    write_component(
        root,
        "row",
        module=_ROW,
        body="<p>{{ label }}</p>",
        loading="<i>loading</i>",
        error="<b>{{ error.kind }}</b>",
    )
    write_component(
        root,
        "owned",
        module=_OWNED,
        body="<p>owned {{ target.username }}</p>",
        loading="<i>loading</i>",
    )
    write_component(
        root,
        "boom",
        module=_BOOM,
        body="<p>{{ value }}</p>",
        loading="<i>loading</i>",
        error="<b data-next-retry>x</b>",
    )
    write_component(
        root,
        "flaky",
        module=_FLAKY,
        body="<p>{{ value }}</p>",
        loading="<i>loading</i>",
        error="<b data-next-retry>f</b>",
    )
    write_page(
        root,
        "home",
        _PUBLIC,
        body='<main>{% component "card" sku="a" n=3 %}{% component "boom" %}</main>',
    )
    write_page(root, "elsewhere", _PUBLIC, body='{% component "card" sku="a" n=3 %}')
    write_page(
        root,
        "guarded",
        _PUBLIC,
        body='{% has_perm "auth.view_user" %}{% component "locked" sku="z" %}'
        "{% endhas_perm %}",
    )
    write_page(
        root, "rows/[int:id]", _ROWS_PAGE, body='{% component "row" target=target %}'
    )
    write_page(
        root, "owned/[int:id]", _ROWS_PAGE, body='{% component "owned" target=target %}'
    )
    write_page(root, "private", _PRIVATE, body='{% component "card" sku="p" n=1 %}')
    write_page(root, "flaky", _PUBLIC, body='{% component "flaky" q="x" %}')
    write_component(
        root,
        "missing",
        module=_MISSING,
        body="<p>{{ value }}</p>",
        loading="<i>loading</i>",
        error="<b>{{ error.kind }}</b>",
    )
    write_page(root, "missing", _PUBLIC, body='{% component "missing" %}')
    write_page(root, "rendered", _RENDERED)
    write_page(
        root, "extra", _PUBLIC, body='{% component "card" sku="a" n=1 colour="red" %}'
    )
    write_component(
        root, "peek", module=_PEEK, body="<p>{{ secret }}</p>", loading="<i>loading</i>"
    )
    write_component(
        root,
        "gated",
        module=_GATED,
        body="<p>{{ label }}</p>",
        loading="<i>loading</i>",
        error="<b>{{ error.kind }}</b>",
    )
    peek = '{% component "peek" uid=7 %}'
    write_page(root, "gp", _STAFF_GATE, body=peek)
    write_page(
        root,
        "shown",
        _PUBLIC,
        body="{% if request.user.is_staff %}" + peek + "{% endif %}",
    )
    write_page(root, "ruled", _PUBLIC, body='{% component "locked" sku="z" %}')
    write_page(root, "gn", _MISSING_GATE, body=peek)
    write_page(root, "ga", _ASYNC_GATE, body=peek)
    write_page(root, "gf", _FLAGGED_GATE, body=peek)
    write_page(root, "vault", _INHERITED_GATE, body="<p>vault</p>")
    write_page(root, "vault/inner", _PUBLIC, body=peek)
    write_page(root, "staff", _STAFF_PAGE, body=peek)
    write_page(
        root, "gated/[int:id]", _ROWS_PAGE, body='{% component "gated" target=target %}'
    )
    write_page(root, "echo", _PUBLIC, body=_ECHO)
    write_component(
        root,
        "search",
        module=_SEARCH,
        body="<p>{{ found }}</p>",
        loading="<i>searching</i>",
        error="<b>{{ error.kind }}</b>",
    )
    write_page(root, "search", _SEARCH_PAGE, body='{% component "search" q=q %}')
    write_component(
        root,
        "ratio",
        module=_RATIO,
        body="<p>{{ shown }}</p>",
        loading="<i>measuring</i>",
        error="<b>{{ error.kind }}</b>",
    )
    write_page(root, "ratio", _RATIO_PAGE, body='{% component "ratio" x=x d=d %}')
    return root


@contextmanager
def _routed(pages: Path, **tokens: object) -> Generator[None]:
    defer = {"TOKENS": {"OPTIONS": tokens}} if tokens else {}
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS, DEFER=defer):
        yield


def _shell(client: Client, url: str) -> tuple[str, list[re.Match[str]]]:
    html = client.get(url).content.decode()
    return html, list(_WRAPPER.finditer(html))


def _degraded(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the records of a boundary degraded to a stream, as `caplog` holds them.

    The inline-assembly notice logs once per process, so a test may capture it or not.
    """
    return [
        record
        for record in caplog.records
        if "renders as a stream boundary" in record.getMessage()
    ]


def _reload(
    client: Client, url: str, found: re.Match[str], **headers: str
) -> HttpResponse:
    sent = {
        "X-Next-Request": "1",
        "X-Next-Zone": found["id"],
        "X-Next-Boundary": found["token"],
        **headers,
    }
    return client.get(url, headers=sent)


def _op(response: HttpResponse) -> dict[str, object]:
    return json.loads(response.content)["ops"][0]


def _user(name: str, *perms: str) -> User:
    user = User.objects.create_user(name, password="x")
    for codename in perms:
        user.user_permissions.add(Permission.objects.get(codename=codename))
    return User.objects.get(pk=user.pk)


def _logged(user: User) -> Client:
    client = Client()
    client.force_login(user)
    return client


class TestShell:
    """A client-trip call leaves its idle wrapper, its token and its loading UI."""

    def test_each_mode_is_written_on_its_wrapper(self, pages: Path) -> None:
        target = _user("target")
        with _routed(pages):
            _html, home = _shell(Client(), "/home/")
            _html, rows = _shell(Client(), f"/rows/{target.pk}/")
        assert [found["mode"] for found in home] == ["visible", "load"]
        assert [found["mode"] for found in rows] == ["manual"]

    def test_the_wrapper_is_idle_and_carries_its_loading_ui(self, pages: Path) -> None:
        with _routed(pages):
            html, (card, _boom) = _shell(Client(), "/home/")
        assert card["rest"] == ' data-next-state="idle"'
        assert "aria-busy" not in html
        assert "data-next-carrier" not in html
        assert "<i>loading a</i>" in html
        assert "<!--b-" not in html

    def test_no_context_function_runs_in_the_shell(self, pages: Path) -> None:
        with _routed(pages):
            Client().get("/home/")
        assert host.RUNS == []

    def test_an_unbound_token_leaves_the_page_shareable(self, pages: Path) -> None:
        with _routed(pages):
            response = Client().get("/home/")
        assert "private" not in response.get("Cache-Control", "")

    def test_a_user_bound_token_makes_the_shell_private(self, pages: Path) -> None:
        with _routed(pages, BINDING="user"):
            response = _logged(_user("viewer")).get("/home/")
        assert "private" in response["Cache-Control"]

    def test_a_query_carrier_is_named_on_the_wrapper(self, pages: Path) -> None:
        with _routed(pages, CARRIER="query"):
            html = Client().get("/home/").content.decode()
        assert 'data-next-carrier="query:_nb"' in html

    def test_a_denied_call_leaves_nothing_and_mints_nothing(
        self, pages: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        minted: list[object] = []
        with _routed(pages):
            backend = defer_manager.tokens()
            monkeypatch.setattr(backend, "issue", lambda *a, **k: minted.append(a))
            html, found = _shell(_logged(_user("viewer", "view_user")), "/guarded/")
        assert found == []
        assert "data-next-boundary" not in html
        assert minted == []

    def test_an_object_rule_runs_on_the_prop_in_the_shell(self, pages: Path) -> None:
        target = _user("target")
        backends = ["tests.support.hosts.ObjectBackend"]
        with override_settings(AUTHENTICATION_BACKENDS=backends), _routed(pages):
            _html, denied = _shell(_logged(_user("plain")), f"/owned/{target.pk}/")
            client = _logged(_user("editor", "change_user"))
            _html, (found,) = _shell(client, f"/owned/{target.pk}/")
            response = _reload(client, f"/owned/{target.pk}/", found)
        assert denied == []
        assert _op(response)["html"] == "<p>owned target</p>"

    def test_an_undeclared_prop_is_refused_when_rendered(self, pages: Path) -> None:
        with _routed(pages), pytest.raises(TemplateSyntaxError, match=r"next\.E224"):
            Client().get("/extra/")

    def test_a_visitor_without_a_session_gets_the_boundary_streamed(
        self, pages: Path
    ) -> None:
        with _routed(pages, BINDING="session"):
            html, found = _shell(Client(raise_request_exception=False), "/home/")
        assert found == []
        assert "<p>card a x6</p>" in html
        assert 'data-next-state="ready"' in html

    def test_a_reader_without_scripts_follows_the_link_to_the_content(
        self, pages: Path
    ) -> None:
        with _routed(pages):
            shell = Client().get("/elsewhere/?q=1").content.decode()
            [href] = re.findall(r'<noscript><a href="([^"]+)" rel="nofollow"', shell)
            page = Client().get(f"/elsewhere/{unescape(href)}").content.decode()
        assert href == "?q=1&amp;_nojs=1"
        assert "<p>card a x6</p>" in page
        assert "data-next-token" not in page
        assert "<noscript><a" not in page
        assert host.RUNS == [("a", 3)]

    def test_a_failed_stream_component_offers_a_retry_token(self, pages: Path) -> None:
        with _routed(pages):
            html = Client(raise_request_exception=False).get("/flaky/").content.decode()
        assert 'data-next-state="error"' in html
        assert "data-next-token=" in html
        assert "data-next-boundary-exp" not in html


def _noise(size: int, first: int, letters: int) -> str:
    """Return `size` letters from `letters` code points zlib cannot shrink much."""
    raw = hashlib.shake_256(b"noise").digest(size)
    return "".join(chr(first + byte % letters) for byte in raw)


class TestOversizedProps:
    """Props a request makes too large for the token deliver the boundary in place."""

    @pytest.mark.parametrize(
        ("asked", "named"),
        [
            pytest.param(_noise(1900, 0x410, 64), "over MAX_BYTES", id="cyrillic"),
            pytest.param(_noise(1900, 0x30, 64), "over MAX_BYTES", id="token"),
            pytest.param("x" * 3000, "over 2048 characters", id="prop"),
        ],
    )
    def test_a_long_query_renders_the_boundary_in_the_response(
        self, pages: Path, caplog: pytest.LogCaptureFixture, asked: str, named: str
    ) -> None:
        reset_failure_logs()
        with _routed(pages), caplog.at_level(logging.WARNING, logger="next.defer"):
            response = Client().get(f"/search/?{urlencode({'q': asked})}")
        html = response.content.decode()
        assert response.status_code == 200
        assert "data-next-token" not in html
        assert f"<p>found {len(asked)}</p>" in html
        assert 'data-next-state="ready"' in html
        (record,) = _degraded(caplog)
        assert record.levelno == logging.WARNING
        assert "'search'" in record.getMessage()
        assert named in record.getMessage()

    def test_a_short_query_keeps_its_client_trip(self, pages: Path) -> None:
        with _routed(pages):
            html, (found,) = _shell(Client(), "/search/?q=tea")
        assert found["mode"] == "visible"
        assert "<i>searching</i>" in html
        assert "found 3" not in html

    def test_a_prop_no_token_carries_still_fails_the_render(self, pages: Path) -> None:
        with _routed(pages), pytest.raises(BoundaryPropsError, match="carries no dict"):
            Client().get("/search/?q=tea&as=mapping")

    @override_settings(DEBUG=True)
    @pytest.mark.parametrize(
        ("url", "listed"),
        [
            pytest.param("/search/?q=tea", None, id="trip"),
            pytest.param(f"/search/?q={'x' * 3000}", "search=stream", id="oversized"),
            pytest.param("/ratio/?x=inf", "ratio=stream", id="non_finite"),
        ],
    )
    def test_debug_lists_a_degraded_boundary_as_streamed(
        self, pages: Path, url: str, listed: str | None
    ) -> None:
        with _routed(pages):
            response = Client().get(url)
        assert response.get("X-Next-Boundaries") == listed


class TestNonFiniteProps:
    """A number from the request that no token carries delivers the boundary in place."""

    @pytest.mark.parametrize(
        ("query", "shown", "named"),
        [
            pytest.param("x=inf", "ratio inf 1", "holds inf", id="float-inf"),
            pytest.param("x=-inf", "ratio -inf 1", "holds -inf", id="float-minus"),
            pytest.param("x=nan", "ratio nan 1", "holds nan", id="float-nan"),
            pytest.param("d=NaN", "ratio 1.0 NaN", "holds NaN", id="decimal-nan"),
            pytest.param(
                "d=Infinity", "ratio 1.0 Infinity", "holds Infinity", id="decimal-inf"
            ),
        ],
    )
    def test_a_non_finite_query_renders_the_boundary_in_the_response(
        self,
        pages: Path,
        caplog: pytest.LogCaptureFixture,
        query: str,
        shown: str,
        named: str,
    ) -> None:
        reset_failure_logs()
        with _routed(pages), caplog.at_level(logging.WARNING, logger="next.defer"):
            response = Client().get(f"/ratio/?{query}")
        html = response.content.decode()
        assert response.status_code == 200
        assert "data-next-token" not in html
        assert f"<p>{shown}</p>" in html
        assert 'data-next-state="ready"' in html
        (record,) = _degraded(caplog)
        assert record.levelno == logging.WARNING
        assert "'ratio'" in record.getMessage()
        assert "a number no token carries" in record.getMessage()
        assert named in record.getMessage()

    def test_a_finite_query_keeps_its_client_trip(self, pages: Path) -> None:
        with _routed(pages):
            html, (found,) = _shell(Client(), "/ratio/?x=1.5&d=2.25")
        assert found["mode"] == "visible"
        assert "<i>measuring</i>" in html
        assert "ratio 1.5" not in html


class TestReload:
    """A reload redeems the token and answers the component alone in an envelope."""

    def test_the_round_trip_renders_the_component_from_its_props(
        self, pages: Path
    ) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            response = _reload(client, "/home/", card)
        assert response.status_code == 200
        assert _op(response) == {
            "op": "morph",
            "target": {"boundary": card["id"]},
            "html": "<p>card a x6</p>",
            "s": "ready",
        }
        assert host.RUNS == [("a", 3)]
        assert response["Cache-Control"] == "private, no-store"
        assert response["Referrer-Policy"] == "same-origin"
        assert "X-Next-Boundary" in response["Vary"]

    def test_a_failed_reload_answers_its_error_ui_with_a_retry_token(
        self, pages: Path
    ) -> None:
        client = Client(raise_request_exception=False)
        with _routed(pages):
            _html, (_card, boom) = _shell(client, "/home/")
            op = _op(_reload(client, "/home/", boom))
        assert op["s"] == "error"
        assert op["k"] == "error"
        assert op["html"] == "<b data-next-retry>x</b>"
        assert isinstance(op["token"], str)
        assert "exp" not in op

    def test_an_intended_failure_answers_its_error_ui(self, pages: Path) -> None:
        client = Client()
        with _routed(pages):
            _html, (found,) = _shell(client, "/missing/")
            op = _op(_reload(client, "/missing/", found))
        assert op["s"] == "error"
        assert op["k"] == "not_found"
        assert op["html"] == "<b>not_found</b>"

    def test_a_page_with_a_render_function_serves_a_reload(self, pages: Path) -> None:
        with _routed(pages):
            reloaded = NextClient().get_boundary("/rendered/", "card", sku="r", n=2)
        assert reloaded.html == "<p>card r x4</p>"

    def test_model_props_are_fetched_again(self, pages: Path) -> None:
        target = _user("target")
        client = Client()
        url = f"/rows/{target.pk}/"
        with _routed(pages):
            _html, (found,) = _shell(client, url)
            User.objects.filter(pk=target.pk).update(username="renamed")
            fresh = _op(_reload(client, url, found))
            User.objects.filter(pk=target.pk).delete()
            gone = _op(_reload(client, url, found))
        assert fresh["html"] == "<p>row renamed</p>"
        assert gone["s"] == "error"
        assert gone["k"] == "not_found"
        assert gone["html"] == "<b>not_found</b>"

    def test_a_spilled_prop_past_max_bytes_round_trips(self, pages: Path) -> None:
        sku = "s" * 3000
        write_page(
            pages, "long", _PUBLIC, body=f'{{% component "card" sku="{sku}" n=1 %}}'
        )
        client = Client()
        with _routed(pages, STORAGE="next.defer.SpillStorage"):
            _html, (card,) = _shell(client, "/long/")
            response = _reload(client, "/long/", card)
        assert _op(response)["html"] == f"<p>card {sku} x2</p>"

    def test_an_expired_token_answers_the_reload_signal(self, pages: Path) -> None:
        client = Client()
        with _routed(pages, MAX_AGE=60):
            with freeze_token_clock(1_000_000):
                _html, (card, _boom) = _shell(client, "/home/")
            with freeze_token_clock(1_000_060):
                response = _reload(client, "/home/", card)
        assert response.status_code == 409
        assert response["X-Next-Reload"] == "token_expired"
        assert response.content == b""

    def test_a_body_carrier_reload_passes_csrf(self, pages: Path) -> None:
        client = Client(enforce_csrf_checks=True)
        # The layout holds no scripts slot, so the page sets no cookie of its own.
        client.cookies["csrftoken"] = csrf = "s" * 32
        with _routed(pages, CARRIER="body"):
            _html, (card, _boom) = _shell(client, "/home/")
            headers = {"X-Next-Request": "1", "X-Next-Zone": card["id"]}
            refused = client.post("/home/", {"_nb": card["token"]}, headers=headers)
            headers["X-CSRFToken"] = csrf
            answered = client.post("/home/", {"_nb": card["token"]}, headers=headers)
        assert refused.status_code == 403
        assert answered.status_code == 200
        assert _op(answered)["html"] == "<p>card a x6</p>"


_PREFETCH = {
    "X-Next-Request": "1",
    "X-Next-Navigate": "1",
    "X-Next-Prefetch": "1",
    "X-Next-Origin": "/",
    "Sec-Fetch-Site": "same-origin",
}


class TestPrefetch:
    """A prefetch that left a client-trip wrapper is not the page a navigation answers."""

    def _prefetch(self, pages: Path, url: str, depth: str = "1") -> dict[str, object]:
        headers = {**_PREFETCH, "X-Next-Prefetch": depth}
        with routed(pages, COMPONENT_BACKENDS=COMPONENTS, ROUTER={"ENABLED": True}):
            response = Client().get(url, headers=headers)
        assert response["Cache-Control"] == "private, no-store"
        return json.loads(response.content)

    @pytest.mark.parametrize("depth", ["1", "full"])
    def test_a_client_trip_wrapper_leaves_the_prefetch_incomplete(
        self, pages: Path, depth: str
    ) -> None:
        envelope = self._prefetch(pages, "/home/", depth)
        assert "data-next-token" in envelope["ops"][0]["html"]
        assert "complete" not in envelope


class TestRefusal:
    """Every refusal answers one identical 403, and the page access comes first."""

    def _refused(self, response: HttpResponse) -> None:
        assert response.status_code == 403
        assert response.content == b""
        assert response["Cache-Control"] == "private, no-store"
        assert response["X-Next-Reload"] == "refused"

    def test_a_tampered_token_is_refused(self, pages: Path) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            token = card["token"]
            swapped = "A" if token[-1] != "A" else "B"
            response = client.get(
                "/home/",
                headers={
                    "X-Next-Request": "1",
                    "X-Next-Zone": card["id"],
                    "X-Next-Boundary": token[:-1] + swapped,
                },
            )
        self._refused(response)

    def test_a_token_of_another_url_is_refused(self, pages: Path) -> None:
        client = Client()
        with _routed(pages):
            _html, (card,) = _shell(client, "/elsewhere/")
            response = _reload(client, "/home/", card)
        self._refused(response)

    @pytest.mark.parametrize(
        ("component", "scope"),
        [
            pytest.param("ghost", "", id="unknown"),
            pytest.param("card", "x", id="scope"),
        ],
    )
    def test_a_token_of_a_component_the_page_does_not_hold_is_refused(
        self, pages: Path, component: str, scope: str
    ) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            claims = BoundaryClaims.of(component, scope, url_path="/home/")
            request = client.get("/home/").wsgi_request
            token = issue_boundary_token(request, claims)
            response = client.get(
                "/home/",
                headers={
                    "X-Next-Request": "1",
                    "X-Next-Zone": card["id"],
                    "X-Next-Boundary": token,
                },
            )
        self._refused(response)

    def test_a_token_of_another_user_is_refused(self, pages: Path) -> None:
        with _routed(pages, BINDING="user"):
            _html, (card, _boom) = _shell(_logged(_user("owner")), "/home/")
            response = _reload(_logged(_user("thief")), "/home/", card)
        self._refused(response)

    @pytest.mark.parametrize(
        "headers",
        [
            pytest.param({"Sec-Fetch-Site": "cross-site"}, id="cross-site"),
            pytest.param({"X-Next-Zone": "list"}, id="wrapper-of-another-shape"),
        ],
    )
    def test_a_reload_out_of_its_page_is_refused(
        self, pages: Path, headers: dict[str, str]
    ) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            response = _reload(client, "/home/", card, **headers)
        self._refused(response)

    def test_a_token_without_the_partial_switch_never_reaches_the_reload(
        self, pages: Path
    ) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            response = client.get(
                "/home/",
                headers={"X-Next-Zone": card["id"], "X-Next-Boundary": card["token"]},
            )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")

    def test_a_page_the_visitor_may_not_open_refuses_before_the_token(
        self, pages: Path
    ) -> None:
        with _routed(pages), capture_signals(boundary_token_rejected) as recorder:
            response = Client().get(
                "/private/",
                headers={
                    "X-Next-Request": "1",
                    "X-Next-Zone": "b-0a0b0c0d-0",
                    "X-Next-Boundary": "garbage",
                },
            )
        assert response.status_code == 302
        assert len(recorder) == 0

    def test_the_rule_runs_again_for_a_revoked_permission(self, pages: Path) -> None:
        user = _user("viewer", "view_user", "change_user")
        client = _logged(user)
        with _routed(pages):
            _html, (found,) = _shell(client, "/guarded/")
            allowed = _reload(client, "/guarded/", found)
            user.user_permissions.remove(Permission.objects.get(codename="change_user"))
            response = _reload(client, "/guarded/", found)
        assert _op(allowed)["html"] == "<p>locked z</p>"
        self._refused(response)

    def test_the_recorded_has_perm_names_run_again(self, pages: Path) -> None:
        user = _user("viewer", "view_user", "change_user")
        client = _logged(user)
        with _routed(pages):
            _html, (found,) = _shell(client, "/guarded/")
            user.user_permissions.remove(Permission.objects.get(codename="view_user"))
            response = _reload(client, "/guarded/", found)
        self._refused(response)


def _staff() -> User:
    return User.objects.create_user("staff", password="x", is_staff=True)


def _sent(found: re.Match[str]) -> dict[str, str]:
    return {
        "X-Next-Request": "1",
        "X-Next-Zone": found["id"],
        "X-Next-Boundary": found["token"],
    }


def _private_headers(response: HttpResponse) -> tuple[str, str, bool]:
    return (
        response["Cache-Control"],
        response["Referrer-Policy"],
        "X-Next-Boundary" in response["Vary"],
    )


_PRIVATE_HEADERS = ("private, no-store", "same-origin", True)


def _shape(response: HttpResponse) -> tuple[object, ...]:
    return (
        response.status_code,
        response.content,
        response["Content-Type"],
        *_private_headers(response),
    )


class TestPageGuards:
    """A reload runs the context guards of the page before its component."""

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            pytest.param("/gp/", 403, id="permission-denied"),
            pytest.param("/gn/", 404, id="not-found"),
            pytest.param("/ga/", 403, id="async"),
            pytest.param("/gf/", 403, id="declared-guard"),
            pytest.param("/vault/inner/", 403, id="inherited"),
        ],
    )
    def test_an_unbound_replayed_token_meets_the_guard_of_the_page(
        self, pages: Path, url: str, status: int
    ) -> None:
        with _routed(pages, BINDING="none"):
            _html, (found,) = _shell(_logged(_staff()), url)
            host.RUNS.clear()
            other = _logged(_user("other"))
            page = other.get(url)
            replayed = other.get(url, headers=_sent(found))
            anonymous = Client().get(url, headers=_sent(found))
        assert page.status_code == status
        assert (replayed.status_code, replayed.content) == (status, page.content)
        assert _shape(anonymous) == _shape(replayed)
        assert _private_headers(replayed) == _PRIVATE_HEADERS
        assert replayed.get("X-Next-Reload") == ("refused" if status == 403 else None)
        assert host.RUNS == []

    @pytest.mark.parametrize("url", ["/gp/", "/gn/"])
    def test_the_default_binding_refuses_a_token_another_visitor_replays(
        self, pages: Path, url: str
    ) -> None:
        with _routed(pages):
            _html, (found,) = _shell(_logged(_staff()), url)
            host.RUNS.clear()
            replayed = _logged(_user("other")).get(url, headers=_sent(found))
        assert (replayed.status_code, replayed.content) == (403, b"")
        assert replayed.get("X-Next-Reload") == "refused"
        assert host.RUNS == []

    def test_the_visitor_the_guard_admits_gets_the_component(self, pages: Path) -> None:
        staff = _logged(_staff())
        with _routed(pages):
            _html, (found,) = _shell(staff, "/gp/")
            response = staff.get("/gp/", headers=_sent(found))
        assert _op(response)["html"] == "<p>secret 7</p>"

    def test_a_garbage_token_is_refused_before_any_guard(self, pages: Path) -> None:
        with _routed(pages):
            response = Client().get(
                "/gp/",
                headers={
                    "X-Next-Request": "1",
                    "X-Next-Zone": "b-0a0b0c0d-0",
                    "X-Next-Boundary": "garbage",
                },
            )
        assert response.status_code == 403
        assert response.content == b""

    def test_a_page_refusal_that_raises_is_sent_private(self, pages: Path) -> None:
        with _routed(pages):
            response = _logged(_user("plain")).get(
                "/staff/",
                headers={
                    "X-Next-Request": "1",
                    "X-Next-Zone": "b-0a0b0c0d-0",
                    "X-Next-Boundary": "garbage",
                },
            )
        assert response.status_code == 403
        assert _private_headers(response) == _PRIVATE_HEADERS
        assert response["X-Next-Reload"] == "refused"


class TestRevokedAccess:
    """A reload checks the access a rule or a guard expresses, never a template branch.

    Each visitor mints a token while allowed, loses the access, then replays it.
    """

    def test_a_template_condition_still_serves_its_token_until_it_expires(
        self, pages: Path
    ) -> None:
        user = _staff()
        client = _logged(user)
        with _routed(pages):
            _html, (found,) = _shell(client, "/shown/")
            User.objects.filter(pk=user.pk).update(is_staff=False)
            _html, after = _shell(client, "/shown/")
            replayed = client.get("/shown/", headers=_sent(found))
        assert after == []
        assert _op(replayed)["html"] == "<p>secret 7</p>"

    def test_an_access_rule_of_the_component_refuses_the_replay(
        self, pages: Path
    ) -> None:
        user = _user("editor", "change_user")
        client = _logged(user)
        with _routed(pages):
            _html, (found,) = _shell(client, "/ruled/")
            user.user_permissions.clear()
            replayed = client.get("/ruled/", headers=_sent(found))
        assert (replayed.status_code, replayed.content) == (403, b"")

    def test_a_guard_of_the_page_refuses_the_replay(self, pages: Path) -> None:
        user = _staff()
        client = _logged(user)
        with _routed(pages):
            _html, (found,) = _shell(client, "/gp/")
            User.objects.filter(pk=user.pk).update(is_staff=False)
            replayed = client.get("/gp/", headers=_sent(found))
        assert replayed.status_code == 403


class TestObjectRule:
    """An object rule runs on the row fetched again, for the visitor of the reload."""

    def test_a_reassigned_owner_holds_the_token_no_more(self, pages: Path) -> None:
        target = _user("target")
        url = f"/owned/{target.pk}/"
        backends = ["tests.support.hosts.OwnerBackend"]
        with override_settings(AUTHENTICATION_BACKENDS=backends), _routed(pages):
            alice, bob = _logged(_user("alice")), _logged(_user("bob"))
            User.objects.filter(pk=target.pk).update(last_name="alice")
            _html, (minted,) = _shell(alice, url)
            _html, denied = _shell(bob, url)
            User.objects.filter(pk=target.pk).update(last_name="bob")
            former = alice.get(url, headers=_sent(minted))
            _html, (fresh,) = _shell(bob, url)
            current = bob.get(url, headers=_sent(fresh))
            reference = Client().get(url, headers=_sent(minted))
        assert denied == []
        assert _shape(former) == _shape(reference)
        assert former.status_code == 403
        assert former.content == b""
        assert _op(current)["html"] == "<p>owned target</p>"

    def test_a_vanished_row_under_an_object_rule_refuses_everyone(
        self, pages: Path
    ) -> None:
        target = _user("target")
        url = f"/owned/{target.pk}/"
        backends = ["tests.support.hosts.ObjectBackend"]
        with override_settings(AUTHENTICATION_BACKENDS=backends), _routed(pages):
            owner = _logged(_user("owner", "change_user"))
            _html, (found,) = _shell(owner, url)
            User.objects.filter(pk=target.pk).delete()
            answers = [
                client.get(url, headers=_sent(found))
                for client in (owner, _logged(_user("plain")), Client())
            ]
        assert {_shape(response) for response in answers} == {
            (403, b"", "text/html; charset=utf-8", *_PRIVATE_HEADERS)
        }

    def test_a_vanished_row_is_named_only_to_a_visitor_the_rule_admits(
        self, pages: Path
    ) -> None:
        target = _user("target")
        viewer = _logged(_user("viewer", "view_user"))
        url = f"/gated/{target.pk}/"
        with _routed(pages):
            _html, (found,) = _shell(viewer, url)
            User.objects.filter(pk=target.pk).delete()
            admitted = _op(viewer.get(url, headers=_sent(found)))
            plain = _logged(_user("plain")).get(url, headers=_sent(found))
            anonymous = Client().get(url, headers=_sent(found))
        assert (admitted["s"], admitted["k"]) == ("error", "not_found")
        assert _shape(plain) == _shape(anonymous)
        assert plain.status_code == 403
        assert plain.content == b""


class TestMethods:
    """A reload answers `GET` and `HEAD`, and `POST` only under the body carrier."""

    @pytest.mark.parametrize("method", ["post", "put", "delete", "options", "patch"])
    def test_another_method_is_not_allowed(self, pages: Path, method: str) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            response = getattr(client, method)("/home/", headers=_sent(card))
        assert response.status_code == 405
        assert response["Allow"] == "GET, HEAD"
        assert _private_headers(response) == _PRIVATE_HEADERS
        assert host.RUNS == []

    def test_a_head_answers_headers_without_rendering(self, pages: Path) -> None:
        client = Client()
        with _routed(pages):
            _html, (card, _boom) = _shell(client, "/home/")
            response = client.head("/home/", headers=_sent(card))
        assert response.status_code == 200
        assert response.content == b""
        assert _private_headers(response) == _PRIVATE_HEADERS
        assert host.RUNS == []

    def test_a_put_under_the_body_carrier_carries_no_token(self, pages: Path) -> None:
        client = Client()
        with _routed(pages, CARRIER="body"):
            _html, (card, _boom) = _shell(client, "/home/")
            response = client.put(
                "/home/",
                data=urlencode({"_nb": card["token"]}),
                content_type="application/x-www-form-urlencoded",
                headers={"X-Next-Request": "1"},
            )
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert "data-next-boundary" in response.content.decode()


class TestReflection:
    """A token in the query string never reaches what the request reads."""

    def test_a_plain_navigation_renders_the_page_without_the_token(
        self, pages: Path
    ) -> None:
        with _routed(pages, CARRIER="query"):
            response = Client().get("/echo/?q=1&_nb=SENTINEL")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert "SENTINEL" not in response.content.decode()
        assert "<p>/echo/?q=1|q=1</p>" in response.content.decode()

    def test_the_login_redirect_of_a_reload_carries_no_token(self, pages: Path) -> None:
        headers = {"X-Next-Request": "1", "X-Next-Zone": "b-0a0b0c0d-0"}
        with _routed(pages, CARRIER="query"):
            reload = Client().get("/private/?q=1&_nb=SENTINEL", headers=headers)
            plain = Client().get("/private/?q=1&_nb=SENTINEL")
        assert reload.status_code == plain.status_code == 302
        assert "SENTINEL" not in reload["Location"]
        assert "SENTINEL" not in plain["Location"]
        assert _private_headers(reload) == _PRIVATE_HEADERS

    def test_a_body_token_without_the_switch_is_ignored(self, pages: Path) -> None:
        client = Client()
        with _routed(pages, CARRIER="body"):
            _html, (card, _boom) = _shell(client, "/home/")
            response = client.post("/home/", {"_nb": card["token"]})
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert host.RUNS == []
