from collections.abc import Callable
from types import TracebackType

import pytest
from django.test import RequestFactory

from next.defer import state as state_module
from next.defer.ledger import ResponseJobs
from next.defer.markers import Caps
from next.defer.render import Processed
from next.defer.state import RequestState, state_of
from next.pages.visits import visit_request
from next.requests import Purpose, mark_purpose


class _Raced:
    """A lock another thread wins once, running `win` before the caller holds it."""

    def __init__(self, win: Callable[[], object]) -> None:
        self.win: Callable[[], object] | None = win

    def __enter__(self) -> None:
        win, self.win = self.win, None
        if win is not None:
            win()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def _session() -> ResponseJobs:
    return ResponseJobs.open(Caps.from_settings())


class TestStateOf:
    """One boundary state per request, kept on `request.next`."""

    def test_the_state_is_made_once_and_kept_on_request_next(
        self, rf: RequestFactory
    ) -> None:
        request = rf.get("/")
        state = state_of(request)
        assert state_of(request) is state
        assert request.next.defer is state

    def test_a_later_purpose_keeps_the_state(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        state = state_of(request)
        mark_purpose(request, Purpose.NAVIGATE)
        assert state_of(request) is state

    def test_a_state_another_thread_made_meanwhile_is_kept(
        self, rf: RequestFactory, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        request = rf.get("/")
        won: list[RequestState] = []
        monkeypatch.setattr(
            state_module, "_lock", _Raced(lambda: won.append(state_of(request)))
        )
        assert state_of(request) is won[0]


class TestVisitState:
    """A visit reads the boundary state of the live request it forked from."""

    def test_a_visit_asking_first_shares_the_state(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        mark_purpose(request, Purpose.NAVIGATE)
        visit = visit_request(request, "/notes/")
        assert state_of(visit) is state_of(request)

    def test_the_live_request_asking_first_shares_the_state(
        self, rf: RequestFactory
    ) -> None:
        request = rf.get("/")
        mark_purpose(request, Purpose.NAVIGATE)
        visit = visit_request(request, "/notes/")
        state = state_of(request)
        assert state_of(visit) is state

    def test_a_visit_of_a_visit_shares_the_state(self, rf: RequestFactory) -> None:
        request = rf.get("/")
        mark_purpose(request, Purpose.NAVIGATE)
        inner = visit_request(visit_request(request, "/notes/"), "/notes/2/")
        assert state_of(inner) is state_of(request)


class TestLazyFields:
    """The fields a job thread reaches are made once, whichever thread asks first."""

    def test_the_held_session_is_opened_once(self) -> None:
        state = RequestState()
        opened: list[ResponseJobs] = []

        def open_one() -> ResponseJobs:
            opened.append(_session())
            return opened[-1]

        assert state.held(open_one) is state.held(open_one) is opened[0]
        assert len(opened) == 1

    def test_a_session_another_thread_opened_meanwhile_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = RequestState()
        winner = _session()
        monkeypatch.setattr(
            state_module, "_lock", _Raced(lambda: state.held(lambda: winner))
        )
        assert state.held(_session) is winner

    def test_the_processor_results_are_made_once(self) -> None:
        state = RequestState()
        assert state.processed(Processed) is state.processed(Processed)

    def test_results_another_thread_made_meanwhile_are_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        state = RequestState()
        winner = Processed()
        monkeypatch.setattr(
            state_module, "_lock", _Raced(lambda: state.processed(lambda: winner))
        )
        assert state.processed(Processed) is winner
