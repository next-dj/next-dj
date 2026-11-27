from pathlib import Path

import pytest
from django.test import Client

from next.components.registry import DeferredComponents
from next.partial.headers import STREAM_ACCEPT
from next.testing import NextClient, boundary_errors, override_next_settings
from tests.support import routed, write_page
from tests.support.access import COMPONENTS
from tests.support.cases.headers import HEADER_CASES, HeaderCase
from tests.support.components import write_component
from tests.support.navigation import declare, layouts_of
from tests.support.streams import streaming


pytestmark = pytest.mark.django_db()


_LAYOUT = (
    "<!DOCTYPE html><html><head>{% collect_styles %}</head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
_SLOW = """
from next.components import component


@component.context("waited")
def waited() -> str:
    return "yes"
"""
_BROKEN = """
from next.components import component


@component.context("value")
def value() -> str:
    raise ValueError("down")
"""
_CARD = """
from next.components import component

component.defer("load")
"""
_CAUGHT = """
from next.pages import context

cache = 60


@context("value")
def value():
    raise ValueError("context")
"""
_SHARED = "cache = 60\n"
_PRIVATE = "x = 1\n"
_SLOW_CALL = "{% component 'slow' %}"
_BROKEN_CALL = "{% component 'broken' %}"
_STREAMED_PAGES = frozenset({"streamed", "both"})


def _zoned(*calls: str) -> str:
    return '<main>{% zone "z" %}<div>' + "".join(calls) + "</div>{% endzone %}</main>"


@pytest.fixture()
def pages(tmp_path: Path, fresh_deferred_components: DeferredComponents) -> Path:
    """Write a page of every outcome, the shared ones declaring a minute of cache."""
    root = tmp_path / "pages"
    root.mkdir()
    (root / "layout.djx").write_text(_LAYOUT)
    (root / "error.djx").write_text("<p>caught</p>")
    write_component(root, "slow", module=_SLOW)
    write_component(root, "broken", module=_BROKEN, loading=None, error="<p>x</p>")
    write_component(root, "card", module=_CARD, body=f"<section>{_SLOW_CALL}</section>")
    write_page(root, "plain", _SHARED, body=_zoned())
    write_page(root, "degraded", _SHARED, body=_zoned(_BROKEN_CALL))
    write_page(root, "caught", _CAUGHT, body=_zoned("{{ value }}"))
    write_page(root, "streamed", _PRIVATE, body=_zoned(_SLOW_CALL))
    write_page(root, "both", _PRIVATE, body=_zoned(_SLOW_CALL, _BROKEN_CALL))
    write_page(root, "card", _PRIVATE, body="<main>{% component 'card' %}</main>")
    return root


def _headers(case: HeaderCase, client: Client) -> dict[str, str]:
    """Return the headers the browser or the client runtime sends for `case`."""
    sent = {"Accept": STREAM_ACCEPT} if case.stream else {}
    if case.kind == "page":
        return sent
    sent["X-Next-Request"] = "1"
    if case.kind == "zone":
        sent["X-Next-Zone"] = "z"
    elif case.kind == "navigate":
        sent["X-Next-Navigate"] = "1"
        sent["X-Next-Layouts"] = declare(layouts_of(client.get("/plain/")))
        sent["X-Next-Origin"] = "/plain/"
        sent["Sec-Fetch-Site"] = "same-origin"
    else:
        sent["X-Next-Zone"] = "b-00000000-0"
        token = NextClient().boundary_token(f"/{case.page}/", case.target)
        sent["X-Next-Boundary"] = token
    return sent


class TestCachingHeaders:
    """Every finish that writes `Cache-Control` adds to what an earlier one set."""

    @pytest.mark.parametrize("case", HEADER_CASES, ids=lambda case: case.id)
    def test_the_final_headers(self, pages: Path, case: HeaderCase) -> None:
        with (
            routed(pages, COMPONENT_BACKENDS=COMPONENTS),
            override_next_settings(ROUTER={"ENABLED": True}),
            streaming(),
            boundary_errors("render"),
        ):
            client = Client()
            response = client.get(f"/{case.page}/", headers=_headers(case, client))
            if response.streaming:
                b"".join(response.streaming_content)
                response.close()
        varied = {name.strip() for name in response["Vary"].split(",")}
        streamed = case.stream or (case.kind == "page" and case.page in _STREAMED_PAGES)
        assert response.streaming is streamed
        assert response.status_code == case.status
        assert response["Cache-Control"] == case.cache_control
        assert varied == case.vary
