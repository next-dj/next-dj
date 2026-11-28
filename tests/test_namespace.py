import pytest

from next.requests import RequestNext
from tests.support.namespace import (
    Access,
    Reach,
    Store,
    accesses,
    framework_sources,
    reaches,
    stores,
)


_REQUESTS_MODULE = "next/requests.py"

# Django names next.dj stores on purpose, each with why it may stay. It only shrinks.
KNOWN: dict[tuple[str, str], str] = {
    ("next/defer/connections/settle.py", "user"): "a lazy user of the settled session",
    ("next/defer/lifecycle.py", "GET"): "the query without the boundary token",
    ("next/defer/lifecycle.py", "POST"): "the body without the boundary token",
    ("next/forms/transport.py", "session"): "a fresh session copy so no write is saved",
    ("next/identity.py", "session"): "a read-only session to load a socket user",
    ("next/pages/visits.py", "GET"): "the query of the visited URL",
    ("next/pages/visits.py", "META"): "the environ of the visited URL",
    ("next/pages/visits.py", "POST"): "a visit is a GET with no body",
    ("next/pages/visits.py", "method"): "a visit is a GET",
    ("next/pages/visits.py", "next"): "the forked namespace of the visit",
    ("next/pages/visits.py", "path"): "the path of the visited URL",
    ("next/pages/visits.py", "path_info"): "the path info of the visited URL",
    ("next/pages/visits.py", "resolver_match"): "a visit resolves its own URL",
    ("next/realtime/topics.py", "user"): "the session user of a realtime stream",
    ("next/testing/client.py", "session"): "the client session on a built request",
    ("next/testing/client.py", "user"): "the client user on a built request",
    ("next/testing/realtime.py", "session"): "the session of a realtime test ticket",
}

OWNERS: dict[str, str] = {
    "info": "next/requests.py",
    "root": "next/requests.py",
    "defer": "next/defer/state.py",
    "boundary_token": "next/defer/lifecycle.py",
    "intent": "next/partial/headers.py",
    "grants": "next/partial/intercept.py",
    "csrf_deferred": "next/csrf.py",
    "csrf_payload": "next/csrf.py",
    "nonce": "next/csrf.py",
    "component_csrf": "next/components/renderers.py",
    "component_lookups": "next/forms/widgets.py",
    "origin_match": "next/forms/origin.py",
    "wizard_loads": "next/forms/wizard.py",
    "dep_cache": "next/deps/cache.py",
    "consent": "next/consent/manager.py",
    "site_origin": "next/site/config.py",
    "site_declared": "next/site/config.py",
    "site_indexable": "next/site/config.py",
    "shared_render": "next/pages/responses.py",
    "personal_render": "next/pages/responses.py",
    "bound_render": "next/pages/responses.py",
    "cookie_vary": "next/pages/responses.py",
    "response_id": "next/pages/layouts.py",
    "metadata": "next/pages/metadata/resolve.py",
    "router_clear": "next/router/navigation/clearing.py",
}

_STORE_ADVICE = (
    "next.dj keeps request state on `request.next`. Add a slot to RequestNext in "
    "next/requests.py and reach it through `next_of` or `held_next`."
)
_OWNER_ADVICE = (
    "Only the owner of a slot touches it. Call the accessor the owner module exports."
)


def store_report(found: list[Store], known: dict[tuple[str, str], str]) -> list[str]:
    """Name every store outside the known names and every known name that is gone."""
    seen = {store.key for store in found}
    new = [store.describe() for store in found if store.key not in known]
    gone = [
        f"{key} no longer occurs, so delete it from KNOWN in tests/test_namespace.py."
        for key in sorted(known.keys() - seen)
    ]
    return [*new, *([_STORE_ADVICE] if new else []), *gone]


def owner_report(found: list[Access], owners: dict[str, str]) -> list[str]:
    """Name every access of a slot outside its owner and every slot its owner leaves."""
    strays = [
        access.describe() + f", which {owners[access.slot]} owns"
        for access in found
        if access.file not in {owners[access.slot], _REQUESTS_MODULE}
    ]
    used = {(access.slot, access.file) for access in found}
    dead = [
        f"{owner} never touches the `{slot}` slot it owns, so drop the slot."
        for slot, owner in owners.items()
        if (slot, owner) not in used
    ]
    return [*strays, *([_OWNER_ADVICE] if strays else []), *dead]


@pytest.fixture(scope="module")
def sources() -> list[tuple[str, str]]:
    return list(framework_sources())


class TestFrameworkStores:
    """Every name next.dj stores on a request outside `request.next` is Django's."""

    def test_no_store_escapes_the_namespace(self, sources) -> None:
        found = [
            store
            for file, source in sources
            if file != _REQUESTS_MODULE
            for store in stores(file, source)
        ]
        lines = store_report(found, KNOWN)
        assert not lines, "\n".join(lines)


class TestSlotOwnership:
    """Only the module that owns a slot of `request.next` reads and writes it."""

    def test_every_slot_has_one_owner(self) -> None:
        assert sorted(OWNERS) == sorted(RequestNext.__slots__)

    def test_no_slot_is_touched_outside_its_owner(self, sources) -> None:
        found = [
            access for file, source in sources for access in accesses(file, source)
        ]
        lines = owner_report(found, OWNERS)
        assert not lines, "\n".join(lines)

    def test_no_area_reaches_the_namespace_by_name(self, sources) -> None:
        found = [
            reach.describe()
            for file, source in sources
            if file != _REQUESTS_MODULE
            for reach in reaches(file, source)
        ]
        assert found == []


def _stores(source: str) -> list[tuple[str, str]]:
    return [(store.attribute, store.how) for store in stores("next/x.py", source)]


class TestStoreScan:
    """The store scan sees each spelling of a write onto a request."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("setattr(request, 'flag', 1)\n", [("flag", "setattr")]),
            ("delattr(self.visit, '_x')\n", [("_x", "delattr")]),
            ("request.__dict__['a'] = 1\n", [("a", "dict")]),
            ("vars(_req).setdefault(requests.NEXT_ATTR, 1)\n", [("next", "dict")]),
            ("a, request.b = 1, 2\n", [("b", "attribute")]),
            ("del visit.path\n", [("path", "attribute")]),
        ],
        ids=["setattr", "delattr", "dunder-dict", "vars", "assignment", "delete"],
    )
    def test_a_store_on_a_request_is_named(self, source, expected) -> None:
        assert _stores(source) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "setattr(response, 'flag', 1)\n",
            "request.META['k'] = 1\n",
            "vars(node).setdefault('a', 1)\n",
            "self.request = request\n",
        ],
        ids=["response", "meta-key", "other-dict", "holder"],
    )
    def test_anything_else_passes(self, source) -> None:
        assert _stores(source) == []

    def test_a_vanished_known_store_is_reported(self) -> None:
        key = ("next/x.py", "user")
        assert store_report([], {key: "why"}) == [
            f"{key} no longer occurs, so delete it from KNOWN in tests/test_namespace.py."
        ]

    def test_a_new_store_names_file_line_and_advice(self) -> None:
        found = stores("next/x.py", "x = 1\nrequest.flag = True\n")
        assert store_report(found, {}) == [
            "next/x.py:2 stores `flag` by attribute",
            _STORE_ADVICE,
        ]


def _accesses(source: str) -> list[tuple[str, bool]]:
    return [(item.slot, item.write) for item in accesses("next/x.py", source)]


class TestAccessScan:
    """The ownership scan follows accessor calls, bound locals and typed parameters."""

    def test_a_call_and_a_bound_local_are_followed(self) -> None:
        source = (
            "def mark(request):\n    next_of(request).consent = 1\n"
            "def read(request):\n    held = held_next(request)\n"
            "    return held.nonce if held else requests.held_next(request).intent\n"
        )
        assert _accesses(source) == [
            ("consent", True),
            ("nonce", False),
            ("intent", False),
        ]

    def test_a_local_bound_in_another_function_is_not(self) -> None:
        source = (
            "def make(request):\n    held = next_of(request)\n"
            "def read(held):\n    return held.nonce\n"
        )
        assert _accesses(source) == []

    def test_a_parameter_typed_as_the_namespace_is_followed(self) -> None:
        source = "def read(held: RequestNext | None):\n    return held.nonce\n"
        assert _accesses(source) == [("nonce", False)]

    def test_a_public_property_and_a_method_are_free(self) -> None:
        source = (
            "def read(request):\n    space = next_of(request)\n"
            "    space.adopt(other)\n    return space.purpose, space.depth\n"
        )
        assert _accesses(source) == []

    def test_a_slot_outside_its_owner_is_named(self) -> None:
        found = accesses("next/x.py", "next_of(request).consent = 1\n")
        lines = owner_report(found, {"consent": "next/consent/manager.py"})
        assert lines[0] == (
            "next/x.py:1 writes the `consent` slot, which next/consent/manager.py owns"
        )
        assert lines[1] == _OWNER_ADVICE

    def test_an_unused_slot_is_named(self) -> None:
        expected = (
            "next/consent/manager.py never touches the `consent` slot it owns, "
            "so drop the slot."
        )
        assert owner_report([], {"consent": "next/consent/manager.py"}) == [expected]

    @pytest.mark.parametrize(
        "source",
        [
            "getattr(request, 'next', None)\n",
            "getattr(obj, requests.NEXT_ATTR)\n",
            "hasattr(request, 'next')\n",
            "request.next.consent\n",
        ],
        ids=["getattr", "getattr-constant", "hasattr", "attribute"],
    )
    def test_a_reach_by_name_is_named(self, source) -> None:
        assert [type(item) for item in reaches("next/x.py", source)] == [Reach]

    def test_another_next_passes(self) -> None:
        assert reaches("next/x.py", "window.next\ngetattr(page, 'prev')\n") == []
