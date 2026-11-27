from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from pathlib import Path

import pytest
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings

from next.lifecycle import (
    Gate,
    Lifecycle,
    PageCall,
    PageFacts,
    Points,
    Share,
    lifecycle,
    register_participant,
)
from next.requests import RequestKind
from tests.support import (
    build_nested_page,
    build_page_request,
    build_zone_request,
    unified_view,
)


_FACTS = PageFacts(
    file_path=Path("page.py"),
    kind=RequestKind.PAGE,
    safe=True,
    template=None,
    chain=None,
    defers=False,
    streams=False,
    guarded=False,
    plain=True,
    atomic=None,
    links=False,
    marks=False,
    fragments=False,
    layers=False,
    fixed=dict,
    slot=None,
    compiler=lambda _template, _links: False,
)


def _opened(_call: PageCall) -> nullcontext[None]:
    return nullcontext()


def _kept(_call: PageCall, response: HttpResponse) -> HttpResponse:
    return response


@dataclass
class _Participant:
    name: str
    claims: RequestKind | None = None
    gated: Gate | None = None
    shared: Share | None = None
    asked: list[RequestKind] = field(default_factory=list)

    def claim(self):
        if self.claims is None:
            return None
        return lambda _request: self.claims

    def gate(self, kind: RequestKind) -> Gate | None:
        self.asked.append(kind)
        return self.gated

    def share(self, facts: PageFacts) -> Share | None:
        return self.shared


@pytest.fixture()
def request_() -> HttpRequest:
    return RequestFactory().get("/")


@pytest.fixture()
def restored() -> Iterator[None]:
    """Give the process registry back as it was, whatever the test registered."""
    held = list(lifecycle)
    yield
    lifecycle.reset()
    for participant in held:
        lifecycle.register(participant)


class TestRegistry:
    """Participants register in order, under unique names, with a moving version."""

    def test_a_participant_registers_last(self) -> None:
        registry = Lifecycle()
        registry.register(_Participant("a"))
        registry.register(_Participant("b"))
        assert [item.name for item in registry] == ["a", "b"]

    def test_a_name_registered_again_keeps_its_place(self) -> None:
        registry = Lifecycle()
        first, second = _Participant("a"), _Participant("b")
        registry.register(first)
        registry.register(second)
        again = _Participant("a")
        registry.register(again)
        assert list(registry) == [again, second]
        assert registry.named("a") is again
        assert registry.named("missing") is None

    def test_every_change_moves_the_version(self) -> None:
        registry = Lifecycle()
        start = registry.version
        registry.register(_Participant("a"))
        registered = registry.version
        registry.forget()
        forgotten = registry.version
        registry.reset()
        assert start < registered < forgotten < registry.version
        assert list(registry) == []

    def test_a_setting_change_asks_the_process_registry_again(self) -> None:
        before = lifecycle.version
        with override_settings(USE_TZ=not settings.USE_TZ):
            pass
        assert lifecycle.version > before

    @pytest.mark.usefixtures("restored")
    def test_a_project_registers_its_own(self) -> None:
        own = _Participant("own")
        register_participant(own)
        assert lifecycle.named("own") is own
        assert list(lifecycle)[-1] is own


class TestKind:
    """A partial request takes the most specific kind any participant claims."""

    def test_no_claim_is_a_page(self, request_) -> None:
        registry = Lifecycle()
        registry.register(_Participant("quiet"))
        assert registry.kind(request_) is RequestKind.PAGE

    def test_the_most_specific_claim_wins_whatever_the_order(self, request_) -> None:
        registry = Lifecycle()
        registry.register(_Participant("nav", claims=RequestKind.NAVIGATE))
        registry.register(_Participant("zone", claims=RequestKind.ZONE))
        registry.register(_Participant("reload", claims=RequestKind.BOUNDARY))
        assert registry.kind(request_) is RequestKind.BOUNDARY

    def test_the_claims_are_read_again_after_a_change(self, request_) -> None:
        registry = Lifecycle()
        registry.register(_Participant("nav", claims=RequestKind.NAVIGATE))
        assert registry.kind(request_) is RequestKind.NAVIGATE
        registry.register(_Participant("zone", claims=RequestKind.ZONE))
        assert registry.kind(request_) is RequestKind.ZONE


class TestGates:
    """The gates of a kind collect in registry order and are asked once."""

    def test_admits_and_refusals_collect_in_order(self) -> None:
        def first(_call):
            return None

        def second(_call):
            return None

        def refuse(_call, denial):
            return denial

        registry = Lifecycle()
        asked = _Participant("a", gated=Gate(admit=first, refuse=refuse))
        registry.register(asked)
        registry.register(_Participant("b", gated=Gate(admit=second, answers=True)))
        registry.register(_Participant("c"))
        gates = registry.gates(RequestKind.ZONE)
        assert gates.admit == (first, second)
        assert gates.refuse == (refuse,)
        assert gates.answers is True
        assert registry.gates(RequestKind.ZONE) is gates
        assert asked.asked == [RequestKind.ZONE]

    def test_no_gate_answers_nothing(self) -> None:
        registry = Lifecycle()
        registry.register(_Participant("a"))
        assert registry.gates(RequestKind.PAGE).answers is False


class TestPoints:
    """Blocks open in registry order, responds and finishes run the other way."""

    def test_a_page_no_participant_takes_gets_the_empty_points(self) -> None:
        registry = Lifecycle()
        registry.register(_Participant("a"))
        assert registry.points(_FACTS) == Points()

    def test_the_order_runs_inward_then_back_out(self) -> None:
        def outer_respond(_call, _html):
            return HttpResponse()

        def inner_respond(_call, _html):
            return HttpResponse()

        def outer_serve(_call):
            return HttpResponse()

        def inner_serve(_call):
            return HttpResponse()

        outer = Share(
            around=_opened,
            block=_opened,
            serve=outer_serve,
            respond=outer_respond,
            finish=_kept,
        )
        inner = replace(outer, serve=inner_serve, respond=inner_respond)
        registry = Lifecycle()
        registry.register(_Participant("outer", shared=outer))
        registry.register(_Participant("inner", shared=inner))
        points = registry.points(_FACTS)
        assert points.around == (_opened, _opened)
        assert points.serve is inner_serve
        assert points.respond is inner_respond
        assert points.finish == (_kept, _kept)


def _served_by(_call: PageCall, response: HttpResponse) -> HttpResponse:
    response["X-Served-By"] = "notes"
    return response


class _ServedBy:
    name = "notes.served-by"

    def claim(self) -> None:
        return None

    def gate(self, _kind: RequestKind) -> Gate | None:
        return None

    def share(self, facts: PageFacts) -> Share | None:
        return Share(finish=_served_by) if facts.kind is RequestKind.PAGE else None


@pytest.mark.usefixtures("restored")
class TestOwnerRecipe:
    """A project participant registered at startup finishes every page it shares."""

    def test_the_document_carries_what_the_participant_set(
        self, page_instance, tmp_path
    ) -> None:
        view = unified_view(page_instance, build_nested_page(tmp_path))
        register_participant(_ServedBy())
        response = view(build_page_request())
        assert response["X-Served-By"] == "notes"

    def test_a_zone_request_it_takes_no_part_in_goes_without(
        self, page_instance, tmp_path
    ) -> None:
        view = unified_view(page_instance, build_nested_page(tmp_path))
        register_participant(_ServedBy())
        response = view(build_zone_request("z"))
        assert not response.has_header("X-Served-By")
