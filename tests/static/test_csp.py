import re
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpResponse
from django.test import Client, override_settings

from tests.support import routed, write_page


csp = pytest.importorskip("django.utils.csp")

LAYOUT = (
    "<html><head>{% collect_head %}{% collect_styles %}<title>t</title></head>"
    "<body>{% template %}{% collect_scripts %}</body></html>"
)
PAGE = """
template = "<p>x</p>"
styles = ["https://cdn.example/a.css"]
scripts = ["https://cdn.example/a.js"]
"""
SCRIPTS = """
from next.scripts import Script, Strategy

scripts = (
    Script("early", init="window.early=1", strategy=Strategy.BLOCKING),
    Script("pixel", src="https://px.example/p.js", init="window.px=[]"),
)
"""
SECURE_CSP = {
    "default-src": [csp.CSP.SELF],
    "script-src": [csp.CSP.SELF, csp.CSP.NONCE],
    "style-src": [csp.CSP.SELF, csp.CSP.NONCE],
}
CSP_MIDDLEWARE = "django.middleware.csp.ContentSecurityPolicyMiddleware"
HEADER_NONCE = re.compile(r"script-src 'self' 'nonce-([^']+)'")
TAG = re.compile(r"<(?:script|style|link)\b[^>]*>", re.IGNORECASE)
NONCE_ATTR = re.compile(r'\snonce="([^"]*)"')


@pytest.fixture(autouse=True)
def _django_csp():
    with override_settings(
        MIDDLEWARE=[*settings.MIDDLEWARE, CSP_MIDDLEWARE], SECURE_CSP=SECURE_CSP
    ):
        yield


def _tree(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "layout.djx").write_text(LAYOUT)
    (root / "scripts.py").write_text(SCRIPTS)
    write_page(root, "", PAGE)
    return root


def _header_nonce(response: HttpResponse) -> str:
    match = HEADER_NONCE.search(response["Content-Security-Policy"])
    assert match is not None
    return match.group(1)


def _tag_nonces(response: HttpResponse) -> list[str | None]:
    tags = TAG.findall(response.content.decode())
    assert len(tags) >= 8
    return [
        match.group(1) if (match := NONCE_ATTR.search(tag)) else None for tag in tags
    ]


class TestDjangoCspMiddleware:
    """Every tag the framework writes carries the nonce Django puts in the header."""

    def test_each_tag_carries_the_header_nonce(self, tmp_path: Path) -> None:
        with routed(_tree(tmp_path / "pages")):
            response = Client().get("/")
        nonce = _header_nonce(response)
        assert (
            "style-src 'self' 'nonce-" + nonce + "'"
            in (response["Content-Security-Policy"])
        )
        assert set(_tag_nonces(response)) == {nonce}

    def test_each_request_gets_a_nonce_of_its_own(self, tmp_path: Path) -> None:
        client = Client()
        with routed(_tree(tmp_path / "pages")):
            first = client.get("/")
            second = client.get("/")
        assert _header_nonce(first) != _header_nonce(second)
        assert set(_tag_nonces(second)) == {_header_nonce(second)}

    def test_a_nonce_switched_off_is_never_minted(self, tmp_path: Path) -> None:
        with routed(_tree(tmp_path / "pages"), CSP_NONCE=False):
            response = Client().get("/")
        assert "nonce-" not in response["Content-Security-Policy"]
        assert set(_tag_nonces(response)) == {None}


class TestANonceOnlyForAWrittenTag:
    """A render mints the nonce only when it writes a tag that carries it."""

    def test_a_page_that_writes_no_tag_stays_shareable(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        root.mkdir(parents=True)
        (root / "layout.djx").write_text(LAYOUT)
        write_page(root, "", 'template = "<p>x</p>"\ncache = 3600\n')
        with routed(root, NEXT_JS_OPTIONS={"policy": "disabled"}):
            response = Client().get("/")
        assert response.status_code == 200
        assert "nonce" not in response.content.decode()
        assert "nonce-" not in response["Content-Security-Policy"]
        assert response["Cache-Control"] == "public, max-age=3600"

    def test_a_page_that_writes_a_tag_turns_private(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        root.mkdir(parents=True)
        (root / "layout.djx").write_text(LAYOUT)
        write_page(root, "", PAGE + "cache = 3600\n")
        with routed(root, NEXT_JS_OPTIONS={"policy": "disabled"}):
            response = Client().get("/")
        assert set(_tag_nonces_of(response)) == {_header_nonce(response)}
        assert "private" in response["Cache-Control"]


def _tag_nonces_of(response: HttpResponse) -> list[str | None]:
    return [
        match.group(1) if (match := NONCE_ATTR.search(tag)) else None
        for tag in TAG.findall(response.content.decode())
    ]
