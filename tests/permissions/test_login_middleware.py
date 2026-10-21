from contextlib import nullcontext
from pathlib import Path

import pytest
from django.test import Client

from next.discovery import discover_page_registrations
from next.forms.manager import form_action_manager
from next.forms.uid import ORIGIN_FIELD_NAME
from next.pages.errors import PageModuleImportError
from tests.support import partial_meta, routed, write_page, write_tree
from tests.support.access import (
    login_required_middleware,
    page_source,
    sign_in,
    write_guard,
)


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("form_registries")]

_LOGIN_GUARD = "page.login_required(inherit=True)\n"
_ZONED = '{% zone "z" %}Z{% endzone %}'

_ACTION = """
from django.http import HttpResponse
from next.forms import action


@action("{name}")
def {name}():
    return HttpResponse("done")
"""


_LOCKED = "from next.components import component\n\ncomponent.login_required()\n"

_CARDS = '[{% component "card" %}{% component "lock" %}]'

_FRAMINGS = {
    "middleware": (login_required_middleware, {}),
    "setting": (nullcontext, {"LOGIN_REQUIRED": True}),
}


@pytest.fixture(params=list(_FRAMINGS))
def site(tmp_path, request) -> Path:
    root = tmp_path / "pages"
    write_page(
        root,
        "open",
        page_source("page.public()\n", _ZONED) + _ACTION.format(name="open_act"),
    )
    write_page(root, "shut", page_source("", _ZONED) + _ACTION.format(name="shut_act"))
    write_guard(root / "fenced", _LOGIN_GUARD)
    write_page(root, "fenced/in", page_source("page.public()\n"))
    write_guard(root / "lifted", _LOGIN_GUARD)
    write_page(
        root,
        "lifted/receipt",
        page_source("page.public(inherit=True)\n", _ZONED)
        + _ACTION.format(name="receipt_act"),
    )
    write_page(
        root,
        "lifted/receipt/admin",
        page_source(_LOGIN_GUARD, _ZONED) + _ACTION.format(name="admin_act"),
    )
    for name, module in (("card", ""), ("lock", _LOCKED)):
        folder = root / "_components" / name
        folder.mkdir(parents=True)
        (folder / "component.py").write_text(module)
        (folder / "component.djx").write_text(f"<b>{name}</b>")
    write_page(root, "shown", page_source("page.public()\n", _CARDS))
    write_page(root, "hidden", page_source("", _CARDS))
    framing, framework = _FRAMINGS[request.param]
    with routed(root, **framework), framing():
        discover_page_registrations()
        yield root


def _post(client: Client, name: str, origin: str):
    url = form_action_manager.get_action_url(name)
    return client.post(url, data={ORIGIN_FIELD_NAME: origin})


class TestPages:
    """The default login lets a public page through and sends the rest to the login."""

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            ("/open/", 200),
            ("/shut/", 302),
            ("/fenced/", 404),
            ("/fenced/in/", 200),
            ("/lifted/receipt/", 200),
            ("/lifted/receipt/admin/", 302),
        ],
        ids=["public", "undeclared", "guard", "page_lift", "lift", "guarded_again"],
    )
    def test_an_anonymous_request(self, site, client, url, status) -> None:
        assert client.get(url).status_code == status

    def test_the_redirect_is_the_one_the_middleware_gives(self, site, client) -> None:
        response = client.get("/shut/?a=1")

        assert response["Location"] == "/accounts/login/?next=/shut/%3Fa%3D1"

    def test_a_signed_in_user_reaches_every_page(self, site, client) -> None:
        sign_in(client, "ada")

        assert [
            client.get(url).status_code for url in ("/open/", "/shut/", "/fenced/in/")
        ] == [200, 200, 200]


class TestZones:
    """A zone request answers to the same rule as its page."""

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            ("/open/", 200),
            ("/shut/", 302),
            ("/lifted/receipt/", 200),
            ("/lifted/receipt/admin/", 302),
        ],
        ids=["public", "undeclared", "lifted", "guarded_again"],
    )
    def test_an_anonymous_zone_request(self, site, client, url, status) -> None:
        response = client.get(url, **partial_meta(zones="z"))

        assert response.status_code == status


class TestComponents:
    """A component renders under the rule of its page and then under its own."""

    @pytest.mark.parametrize(
        ("url", "signed", "answer"),
        [
            ("/shown/", False, (200, b"[<b>card</b>]")),
            ("/hidden/", False, (302, b"")),
            ("/shown/", True, (200, b"[<b>card</b><b>lock</b>]")),
            ("/hidden/", True, (200, b"[<b>card</b><b>lock</b>]")),
        ],
        ids=["public-anonymous", "closed-anonymous", "public-user", "closed-user"],
    )
    def test_a_component_on_a_page(self, site, client, url, signed, answer) -> None:
        if signed:
            sign_in(client, "ada")

        response = client.get(url)

        assert (response.status_code, response.content) == answer


class TestActions:
    """The action endpoint passes the middleware and the dispatcher asks the login."""

    def test_an_action_of_a_public_page_posted_from_it(self, site, client) -> None:
        response = _post(client, "open_act", "/open/")

        assert (response.status_code, response.content) == (200, b"done")

    @pytest.mark.parametrize(
        ("name", "origin"),
        [
            ("shut_act", "/shut/"),
            ("open_act", "/shut/"),
            ("shut_act", "/open/"),
            ("admin_act", "/lifted/receipt/admin/"),
        ],
        ids=["closed_page", "closed_origin", "closed_owner", "guarded_again"],
    )
    def test_an_anonymous_post_is_sent_to_the_login(
        self, site, client, name, origin
    ) -> None:
        response = _post(client, name, origin)

        assert response.status_code == 302
        assert response["Location"] == f"/accounts/login/?next={origin}"

    def test_an_action_of_a_lifted_subtree(self, site, client) -> None:
        response = _post(client, "receipt_act", "/lifted/receipt/")

        assert (response.status_code, response.content) == (200, b"done")

    def test_a_signed_in_user_posts(self, site, client) -> None:
        sign_in(client, "ada")

        assert _post(client, "shut_act", "/shut/").content == b"done"


class TestFrameworkEndpoints:
    """The token endpoint and the SEO files stay open to an anonymous request."""

    def test_the_token_endpoint(self, site, client) -> None:
        response = client.get("/_next/csrf/", **partial_meta())

        assert response.status_code == 200

    @pytest.mark.parametrize("framing", list(_FRAMINGS))
    @pytest.mark.parametrize("url", ["/robots.txt", "/sitemap.xml"])
    def test_the_seo_files(self, tmp_path, client, url, framing) -> None:
        root = write_tree(tmp_path / "seo", sitemap="", robots_txt=b"User-agent: *\n")
        closing, framework = _FRAMINGS[framing]

        with routed(root, **framework), closing():
            assert client.get(url).status_code == 200


_BOOM = "raise RuntimeError('boom')\n"


def _broken_tree(root: Path) -> Path:
    write_page(root, "own", _BOOM, body=_ZONED)
    write_guard(root / "under", _BOOM)
    write_page(
        root, "under/leaf", page_source("", _ZONED) + _ACTION.format(name="leaf_act")
    )
    write_page(
        root, "free", page_source("page.public()\n") + _ACTION.format(name="free_act")
    )
    write_page(root, "under/lifted", page_source("page.public()\n"))
    return root


def _answer(root: Path, framing: str, call, **framework: object):
    closing, extra = _FRAMINGS[framing]
    with routed(root, **extra, **framework), closing():
        discover_page_registrations()
        return call(Client())


def _get(url: str, **meta: object):
    return lambda client: client.get(url, **meta)


def _post_from(name: str, origin: str):
    return lambda client: _post(client, name, origin)


def _signed_in(call, username: str):
    def run(client: Client):
        sign_in(client, username)
        return call(client)

    return run


_BROKEN_CALLS = {
    "own-page": _get("/own/?a=1"),
    "own-zone": _get("/own/", **partial_meta(zones="z")),
    "above-page": _get("/under/leaf/"),
    "above-zone": _get("/under/leaf/", **partial_meta(zones="z")),
    "above-action": _post_from("leaf_act", "/under/leaf/"),
    "broken-origin": _post_from("free_act", "/own/"),
}


class TestBrokenChains:
    """A broken chain sends an anonymous request to log in, the same under both."""

    @pytest.mark.parametrize("where", list(_BROKEN_CALLS))
    @pytest.mark.parametrize("framework", [{}, {"STRICT_LOADING": True}])
    def test_the_anonymous_answer_is_the_same_login(
        self, tmp_path, where, framework
    ) -> None:
        root = _broken_tree(tmp_path / "pages")
        call = _BROKEN_CALLS[where]

        answers = [_answer(root, framing, call, **framework) for framing in _FRAMINGS]

        assert [response.status_code for response in answers] == [302, 302]
        assert answers[0]["Location"] == answers[1]["Location"]
        assert answers[0]["Location"].startswith("/accounts/login/?next=")

    @pytest.mark.parametrize("where", ["own-page", "above-page", "above-action"])
    def test_a_signed_in_user_meets_the_404(self, tmp_path, where) -> None:
        root = _broken_tree(tmp_path / "pages")
        statuses = [
            _answer(
                root, framing, _signed_in(_BROKEN_CALLS[where], framing)
            ).status_code
            for framing in _FRAMINGS
        ]

        assert statuses == [404, 404]

    @pytest.mark.parametrize("framing", list(_FRAMINGS))
    def test_a_public_page_under_a_broken_chain_meets_the_refusal(
        self, tmp_path, framing
    ) -> None:
        root = _broken_tree(tmp_path / "pages")
        call = _get("/under/lifted/")

        assert _answer(root, framing, call).status_code == 404
        with pytest.raises(PageModuleImportError):
            _answer(root, framing, call, STRICT_LOADING=True)

    def test_without_the_default_login_an_anonymous_request_meets_the_404(
        self, tmp_path
    ) -> None:
        root = _broken_tree(tmp_path / "pages")

        with routed(root):
            response = Client().get("/under/leaf/")

        assert response.status_code == 404
