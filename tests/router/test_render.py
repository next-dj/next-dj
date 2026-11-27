import json
from collections.abc import Iterator

import pytest

from next.ports import page_scripts
from next.router.render import (
    PAGE_ASSET_ATTR,
    RouterRender,
    RouterScripts,
    router_render,
    speculation_tag,
)
from next.static.runtime import ROUTER_CHUNK_KEY, ROUTER_PAYLOAD_KEY
from next.testing import override_next_settings
from tests.support.ports import StubRender


ENTRY = '{"scope":"all"}'


@pytest.fixture()
def routed() -> Iterator[RouterRender]:
    held = RouterRender("5ea1", ENTRY, str.strip, speculation='{"prefetch":[]}')
    token = router_render.set(held)
    try:
        yield held
    finally:
        router_render.reset(token)


def _decoded(shared: str) -> dict[str, object]:
    return json.loads(f"{{{shared}}}")


class TestRouterEntry:
    """The `$router` entry adds the bundle URLs and a snapshot refusal per page."""

    def test_a_page_adding_nothing_sends_the_shared_entry(self) -> None:
        held = RouterRender("5ea1", ENTRY)
        assert held.snapshot is True
        assert held.speculation == ""
        assert held.encoded() is ENTRY
        assert held.collector is None
        assert held.payload == {}

    @pytest.mark.parametrize(
        ("boundaries", "intent", "snapshot", "expected"),
        [
            pytest.param(
                "/r.js", None, True, {"scope": "all", "boundaries": "/r.js"}, id="b"
            ),
            pytest.param(
                None, "/i.js", True, {"scope": "all", "intent": "/i.js"}, id="intent"
            ),
            pytest.param(
                None, None, False, {"scope": "all", "snapshot": False}, id="refusal"
            ),
            pytest.param(
                "/r.js",
                "/i.js",
                False,
                {
                    "scope": "all",
                    "boundaries": "/r.js",
                    "intent": "/i.js",
                    "snapshot": False,
                },
                id="all",
            ),
        ],
    )
    def test_the_additions(
        self,
        boundaries: str | None,
        intent: str | None,
        expected: dict[str, object],
        *,
        snapshot: bool,
    ) -> None:
        held = RouterRender("5ea1", ENTRY, snapshot=snapshot)
        held.boundaries = boundaries
        held.intent = intent
        encoded = held.encoded()
        assert json.loads(encoded) == expected
        assert list(json.loads(encoded)) == list(expected)


class TestSpeculationTag:
    """The rules travel in one script element, nonced when the response has one."""

    def test_the_tag(self) -> None:
        assert speculation_tag("{}", None) == (
            '<script type="speculationrules">{}</script>'
        )
        assert speculation_tag("{}", "n1") == (
            '<script type="speculationrules" nonce="n1">{}</script>'
        )


class TestRouterScripts:
    """The router adds its entry, bundles, enclosure and passes to a routed page only."""

    def test_the_registered_provider_is_the_area_one(self) -> None:
        found = [item for item in page_scripts if item.name == "router"]
        assert [type(item) for item in found] == [RouterScripts]
        assert found[0].keys == {ROUTER_PAYLOAD_KEY}

    def test_it_applies_while_the_router_is_on(self) -> None:
        with override_next_settings(ROUTER={"ENABLED": True}):
            assert RouterScripts().applies() is True
        with override_next_settings(ROUTER={"ENABLED": False}):
            assert RouterScripts().applies() is False

    def test_an_unrouted_page_shares_nothing(self) -> None:
        assert RouterScripts().share(StubRender()) is None

    def test_a_routed_page_shares_its_state(self, routed: RouterRender) -> None:
        render = StubRender(nonce="n1")
        render.entries = {"$scripts": []}
        share = RouterScripts().share(render)
        assert share is not None
        assert share.head == speculation_tag('{"prefetch":[]}', "n1")
        assert _decoded(share.shared) == {
            "$router": {
                "scope": "all",
                "boundaries": "/b/boundaries",
                "intent": "/b/intent",
            }
        }
        assert share.eager == (ROUTER_CHUNK_KEY,)
        assert share.stylesheet == PAGE_ASSET_ATTR
        assert share.finish is str.strip
        assert share.enclose is not None
        assert share.enclose("x") == "<!--nx:a 5ea1-->x<!--/nx:a 5ea1-->"
        assert share.after is not None
        assert share.after("n1") == (
            '<script type="application/json" data-next-layouts nonce="n1">'
            '"nx:layouts 5ea1"</script>'
        )
        assert routed.collector is render.collector
        assert routed.payload == {"$scripts": []}

    def test_a_page_without_rules_mints_no_nonce_for_its_head(self) -> None:
        held = RouterRender("5ea1", ENTRY, snapshot=False)
        render = StubRender(bundles=False)
        share = held.share(render)
        assert share.head == ""
        assert render.minted == 0
        assert _decoded(share.shared) == {
            "$router": {"scope": "all", "snapshot": False}
        }

    def test_one_entry_is_encoded_once_and_a_busy_page_set_is_dropped(self) -> None:
        first = RouterRender("5ea1", ENTRY).share(StubRender()).shared
        assert RouterRender("5ea2", ENTRY).share(StubRender()).shared is first
        for index in range(80):
            RouterRender("5ea1", f'{{"scope":"{index}"}}').share(StubRender())
        again = RouterRender("5ea1", ENTRY).share(StubRender()).shared
        assert again == first
        assert again is not first
