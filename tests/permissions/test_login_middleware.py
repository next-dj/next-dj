from pathlib import Path

import pytest
from django.test import Client

from next.discovery import discover_page_registrations
from next.forms.manager import form_action_manager
from next.forms.uid import ORIGIN_FIELD_NAME
from tests.support import (
    isolated_form_registries,
    partial_meta,
    routed,
    write_page,
    write_tree,
)
from tests.support.access import (
    login_required_middleware,
    page_source,
    sign_in,
    write_proxy,
)


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("isolated_declarations")]

_ZONED = '{% zone "z" %}Z{% endzone %}'

_ACTION = """
from django.http import HttpResponse
from next.forms import action


@action("{name}")
def {name}():
    return HttpResponse("done")
"""


@pytest.fixture(autouse=True)
def _form_registries():
    with isolated_form_registries():
        yield


@pytest.fixture()
def site(tmp_path) -> Path:
    root = tmp_path / "pages"
    write_page(
        root,
        "open",
        page_source("page.public()\n", _ZONED) + _ACTION.format(name="open_act"),
    )
    write_page(root, "shut", page_source("", _ZONED) + _ACTION.format(name="shut_act"))
    write_proxy(root / "fenced", "proxy.login_required()\n")
    write_page(root, "fenced", page_source("page.public()\n"))
    write_proxy(root / "lifted", "proxy.login_required()\n")
    write_proxy(root / "lifted" / "receipt", "proxy.public()\n")
    write_page(
        root,
        "lifted/receipt",
        page_source("", _ZONED) + _ACTION.format(name="receipt_act"),
    )
    write_proxy(root / "lifted" / "receipt" / "admin", "proxy.login_required()\n")
    write_page(
        root,
        "lifted/receipt/admin",
        page_source("", _ZONED) + _ACTION.format(name="admin_act"),
    )
    with routed(root), login_required_middleware():
        discover_page_registrations()
        yield root


def _post(client: Client, name: str, origin: str):
    url = form_action_manager.get_action_url(name)
    return client.post(url, data={ORIGIN_FIELD_NAME: origin})


class TestPages:
    """The middleware lets a public page through and sends the rest to the login."""

    @pytest.mark.parametrize(
        ("url", "status"),
        [
            ("/open/", 200),
            ("/shut/", 302),
            ("/fenced/", 200),
            ("/lifted/receipt/", 200),
            ("/lifted/receipt/admin/", 302),
        ],
        ids=["public", "undeclared", "page_lift", "proxy_lift", "guarded_again"],
    )
    def test_an_anonymous_request(self, site, client, url, status) -> None:
        assert client.get(url).status_code == status

    def test_a_signed_in_user_reaches_every_page(self, site, client) -> None:
        sign_in(client, "ada")

        assert [
            client.get(url).status_code for url in ("/open/", "/shut/", "/fenced/")
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


class TestActions:
    """The action endpoint passes the middleware and the dispatcher asks for a login."""

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
        assert response["Location"].startswith("/accounts/login/")

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

    @pytest.mark.parametrize("url", ["/robots.txt", "/sitemap.xml"])
    def test_the_seo_files(self, tmp_path, client, url) -> None:
        root = write_tree(tmp_path / "seo", sitemap="", robots_txt=b"User-agent: *\n")

        with routed(root), login_required_middleware():
            assert client.get(url).status_code == 200
