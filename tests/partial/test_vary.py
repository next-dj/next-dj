from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from django import forms as django_forms
from django.http import HttpRequest, HttpResponseBase, HttpResponseRedirect

from next.forms import Form
from next.partial.headers import ORIGIN, REQUEST_FLAG, VARY_HEADERS, ZONE
from next.suspense.policy import streaming
from next.suspense.tokens.transport import HEADER
from next.testing import NextClient
from tests.support import routed, write_page
from tests.support.access import COMPONENTS


pytestmark = pytest.mark.django_db

_LAYOUT = (
    "<!doctype html><html><head>{% collect_styles %}</head><body>"
    '{% zone "main" %}{% template %}{% endzone %}{% collect_scripts %}</body></html>'
)
_CARD = 'from next.components import component\n\ncomponent.defer("load")\n'
_LATE = (
    "from next.components import component\n\n\n"
    '@component.context("word")\ndef word() -> str:\n    return "late"\n'
)
_PUBLIC = "from next import page\n\npage.public()\n"
_EVENTS = {"Accept": "text/event-stream"}
_WIRE_ID = "b-00000000-0"

_PARTIAL = frozenset(VARY_HEADERS)
_STREAM = frozenset({"Accept-Encoding", "Accept"})
_READ = frozenset({"Accept"})
_ORIGIN = frozenset({ORIGIN})
_ROUTER = {"ENABLED": True}
_NO_LINKS = {"ENABLED": True, "CURRENT": False}


class VaryProbeForm(Form):
    """A form whose result the Vary table reads."""

    name = django_forms.CharField()

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        return HttpResponseRedirect("/b/")


def _reload(client: NextClient) -> HttpResponseBase:
    token = client.boundary_token("/shop/", "card")
    sent = {REQUEST_FLAG: "1", ZONE: _WIRE_ID, HEADER: token}
    return client.get("/shop/", headers=sent)


def _streamed_zone(client: NextClient) -> HttpResponseBase:
    with streaming():
        return client.get_zones("/late/", "main", headers=_EVENTS)


def _streamed_navigation(client: NextClient) -> HttpResponseBase:
    with streaming():
        return client.navigate("/late/", origin="/a/", headers=_EVENTS).response


def _prefetch(client: NextClient) -> HttpResponseBase:
    return client.navigate("/b/", origin="/a/", prefetch=True).response


def _form_result(client: NextClient) -> HttpResponseBase:
    return client.post_action("vary_probe_form", {}, origin="/a/", partial=True)


def _plain_form_result(client: NextClient) -> HttpResponseBase:
    return client.post_action("vary_probe_form", {"name": "x"}, origin="/a/")


@dataclass(frozen=True, slots=True)
class _Kind:
    """One response kind, the request that asks for it, and the headers it varies on."""

    id: str
    send: Callable[[NextClient], HttpResponseBase]
    vary: frozenset[str]
    router: dict[str, object] = field(default_factory=lambda: _ROUTER)
    content_type: str | None = None


_KINDS = (
    _Kind("document", lambda client: client.get("/a/"), _PARTIAL),
    _Kind(
        "streamed document",
        lambda client: client.get_stream("/late/").response,
        _PARTIAL | _STREAM,
    ),
    _Kind(
        "zone",
        lambda client: client.get_zones("/a/", "main"),
        _PARTIAL | _READ | _ORIGIN,
    ),
    _Kind(
        "zone without active links",
        lambda client: client.get_zones("/a/", "main"),
        _PARTIAL | _READ,
        router=_NO_LINKS,
    ),
    _Kind(
        "streamed zone",
        _streamed_zone,
        _PARTIAL | _STREAM | _ORIGIN,
        content_type="text/event-stream",
    ),
    _Kind(
        "navigation envelope",
        lambda client: client.navigate("/b/", origin="/a/").response,
        _PARTIAL,
    ),
    _Kind(
        "streamed navigation",
        _streamed_navigation,
        _PARTIAL | _STREAM,
        content_type="text/event-stream",
    ),
    _Kind("prefetch", _prefetch, _PARTIAL),
    _Kind("form result", _form_result, _PARTIAL),
    _Kind("plain form result", _plain_form_result, frozenset()),
    _Kind("boundary reload", _reload, _PARTIAL | _READ),
)


def _tree(root: Path) -> None:
    for name, module, loading in (("late", _LATE, True), ("card", _CARD, False)):
        folder = root / "_components" / name
        folder.mkdir(parents=True)
        (folder / "component.py").write_text(module)
        (folder / "component.djx").write_text("<p>{{ word }}</p>")
        if loading:
            (folder / "loading.djx").write_text("<p>loading</p>")
    (root / "layout.djx").write_text(_LAYOUT)
    write_page(root, "a", _PUBLIC, body="<h1>A</h1>")
    write_page(root, "b", _PUBLIC, body="<h1>B</h1>")
    write_page(root, "shop", _PUBLIC, body='{% component "card" %}')
    write_page(root, "late", _PUBLIC, body='{% component "late" %}')


def _varied(response: HttpResponseBase) -> frozenset[str]:
    """Return the request headers `Vary` names, the session `Cookie` aside."""
    names = {name.strip() for name in response.get("Vary", "").split(",")}
    return frozenset(names - {"", "Cookie"})


@pytest.mark.usefixtures("fresh_deferred_components")
@pytest.mark.parametrize("kind", _KINDS, ids=[kind.id for kind in _KINDS])
def test_each_response_kind_varies_on_its_request_headers(
    tmp_path: Path, kind: _Kind
) -> None:
    root = tmp_path / "pages"
    _tree(root)
    with routed(root, COMPONENT_BACKENDS=COMPONENTS, ROUTER=kind.router):
        response = kind.send(NextClient())
    response.close()
    if kind.content_type is not None:
        assert response["Content-Type"].startswith(kind.content_type)
    assert _varied(response) == kind.vary
