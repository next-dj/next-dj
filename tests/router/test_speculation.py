import json
import re
from pathlib import Path

import pytest

from next.conf.router import Eagerness, RouterScope
from next.router.speculation import GUARDED, OFF, encoded_rules, rules


CLIENT_ROUTER = Path(__file__).resolve().parents[2] / "next" / "client" / "router.ts"


def _where(found: dict[str, object]) -> list[dict[str, object]]:
    (entry,) = found["prefetch"]
    assert isinstance(entry, dict)
    where = entry["where"]
    assert isinstance(where, dict)
    return where["and"]


class TestRules:
    """The rules prefetch only the same-origin links the router leaves to the browser."""

    def test_the_links_the_router_leaves(self) -> None:
        found = rules(Eagerness.MODERATE, RouterScope.ALL, ("/static/", "/files(1)/"))
        (entry,) = found["prefetch"]
        assert isinstance(entry, dict)
        assert (entry["source"], entry["eagerness"]) == ("document", "moderate")
        same_origin, unowned, guarded = _where(found)
        assert same_origin == {"href_matches": "/*"}
        assert unowned == {
            "or": [
                {"selector_matches": f":is({OFF}),:is({OFF}) *"},
                {"href_matches": "/static/*"},
                {"href_matches": "/files\\(1\\)/*"},
            ]
        }
        assert guarded == {
            "not": {"selector_matches": f":is({GUARDED}),:is({GUARDED}) *"}
        }
        assert "prerender" not in found

    def test_opt_in_leaves_the_links_outside_the_router(self) -> None:
        _, unowned, _ = _where(rules(Eagerness.EAGER, RouterScope.OPT_IN, ()))
        owner = "[data-next-router]"
        assert unowned["or"][-1] == {
            "not": {"selector_matches": f":is({owner}),:is({owner}) *"}
        }

    def test_the_encoding_is_safe_in_a_script(self) -> None:
        text = encoded_rules(Eagerness.EAGER, RouterScope.ALL, ("/a<b>/",))
        assert "<" not in text
        assert ">" not in text
        assert json.loads(text) == rules(Eagerness.EAGER, RouterScope.ALL, ("/a<b>/",))


@pytest.mark.skipif(not CLIENT_ROUTER.exists(), reason="needs the source checkout")
class TestClientAgreement:
    """The links the client router leaves are those the rules guard, in its order."""

    def test_the_client_selector_is_the_guarded_runtime_then_off(self) -> None:
        source = CLIENT_ROUTER.read_text(encoding="utf-8")
        found = re.search(r"^const OFF =(.*?);$", source, re.MULTILINE | re.DOTALL)
        assert found is not None
        client = "".join(re.findall(r'"([^"]*)"', found.group(1))).split(",")
        off = OFF.split(",")
        runtime = client[: len(client) - len(off)]
        assert runtime
        assert client[len(runtime) :] == off
        assert GUARDED.split(",")[: len(runtime)] == runtime
