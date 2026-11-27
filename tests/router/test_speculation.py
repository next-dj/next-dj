import json

from next.conf.router import Eagerness, RouterScope
from next.router.speculation import GUARDED, OFF, encoded_rules, rules


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

    def test_the_pages_scope_matches_the_pages_but_no_other_route(self) -> None:
        found = rules(
            Eagerness.MODERATE, RouterScope.PAGES, (), ["/a/", "/b/*"], ["/a/x/"]
        )
        pages, others, _, _ = _where(found)
        assert pages == {"href_matches": ["/a/", "/b/*"]}
        assert others == {"not": {"href_matches": ["/a/x/"]}}

    def test_the_pages_scope_with_no_other_route_adds_no_refusal(self) -> None:
        found = rules(Eagerness.MODERATE, RouterScope.PAGES, (), ["/a/"])
        assert len(_where(found)) == 3
