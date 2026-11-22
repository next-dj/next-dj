import math
import re
import threading
import time

import pytest

from next.defer import BoundaryOptions, DeferMode
from next.suspense import Job, Server
from next.suspense.markers import (
    Addressing,
    Caps,
    Captured,
    ForegroundFacts,
    Group,
    Site,
)
from next.suspense.session import IDENT, BoundarySession, new_seal, seal_of, wire_id
from tests.support.jobs import plain_job


A = Site(Addressing.COMPONENT, "row", DeferMode.STREAM, "list.djx", 0)
B = Site(Addressing.COMPONENT, "row", DeferMode.STREAM, "list.djx", 1)


def _caps(
    *,
    boundaries: int = 64,
    per_site: int = 4,
    instances: int = 1000,
    per_request: int = 4,
) -> Caps:
    return Caps(10.0, 2.0, boundaries, per_site, 4, instances, per_request)


def _session(caps: Caps) -> BoundarySession[str]:
    session: BoundarySession[str] = BoundarySession(
        caps, seal="0badcafe", deadline=100.0
    )
    return session


def _job(session: BoundarySession[str], site: Site) -> Job[str]:
    return plain_job(
        session.next_id(), site=site, deadline=session.deadline, html="<li>row</li>"
    )


def _queue(session: BoundarySession[str], sites: list[Site]) -> list[Job[str]]:
    jobs = [_job(session, site) for site in sites]
    for job in jobs:
        session.queue(job)
    return jobs


class TestOpening:
    """Tests for ``BoundarySession.open`` and the state a view fills in."""

    def test_open_mints_a_seal_and_counts_the_deadline_from_now(self) -> None:
        caps = _caps()
        before = time.monotonic()
        session = BoundarySession[str].open(caps)
        after = time.monotonic()
        assert re.fullmatch(r"[0-9a-f]{8}", session.seal)
        assert before + caps.deadline <= session.deadline <= after + caps.deadline
        assert session.caps is caps

    def test_two_sessions_carry_different_seals(self) -> None:
        caps = _caps()
        assert BoundarySession[str].open(caps).seal != (
            BoundarySession[str].open(caps).seal
        )

    def test_a_new_session_holds_nothing(self) -> None:
        session = _session(_caps())
        assert session.jobs == ()
        assert session.facts is None
        assert session.threads == ()
        assert session.connections == ()
        assert session.snapshot is None
        assert session.scope is None
        assert session.plan().units == ()
        assert session.plan().expired == ()


class TestIds:
    """Tests for ``BoundarySession.next_id``."""

    def test_ids_count_up_under_the_seal(self) -> None:
        session = _session(_caps())
        assert [session.next_id() for _ in range(3)] == [
            "b-0badcafe-0",
            "b-0badcafe-1",
            "b-0badcafe-2",
        ]

    def test_threads_never_share_an_id(self) -> None:
        session = _session(_caps())
        minted: list[list[str]] = [[] for _ in range(8)]

        def mint(into: list[str]) -> None:
            into.extend(session.next_id() for _ in range(200))

        threads = [threading.Thread(target=mint, args=(into,)) for into in minted]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        ids = [one for into in minted for one in into]
        assert len(ids) == 1600
        assert len(set(ids)) == 1600


class TestDeadline:
    """Tests for ``BoundarySession.deadline_for``."""

    def test_no_timeout_takes_the_response_deadline(self) -> None:
        assert _session(_caps()).deadline_for(BoundaryOptions(), 40.0) == 100.0

    def test_a_short_timeout_wins(self) -> None:
        options = BoundaryOptions(timeout=2.0)
        assert _session(_caps()).deadline_for(options, 40.0) == 42.0

    def test_the_response_deadline_bounds_a_long_timeout(self) -> None:
        options = BoundaryOptions(timeout=90.0)
        assert _session(_caps()).deadline_for(options, 40.0) == 100.0


class TestQueue:
    """Tests for ``BoundarySession.queue`` and ``BoundarySession.plan``."""

    def test_jobs_keep_document_order_and_site_counts(self) -> None:
        session = _session(_caps())
        jobs = _queue(session, [A, B, A])
        assert session.jobs == tuple(jobs)
        assert (session.instances(A), session.instances(B)) == (2, 1)
        assert session.instances(Site(Addressing.PAGE, "/", DeferMode.STREAM, "p")) == 0

    def test_instances_past_the_site_cap_form_groups_in_document_order(self) -> None:
        session = _session(_caps(per_site=2, per_request=3))
        jobs = _queue(session, [A, A, B, *[A] * 7])
        a0, a1, b0, *rest = jobs
        assert session.plan().units == (
            a0,
            a1,
            b0,
            Group(A, tuple(rest[0:3])),
            Group(A, tuple(rest[3:6])),
            Group(A, tuple(rest[6:7])),
        )

    @pytest.mark.parametrize("count", [1, 4, 5, 8, 9, 17, 40])
    def test_the_overflow_of_a_site_makes_ceil_n_over_per_request_groups(
        self, count: int
    ) -> None:
        session = _session(_caps(per_site=4, per_request=4))
        _queue(session, [A] * count)
        units = session.plan().units
        groups = [unit for unit in units if isinstance(unit, Group)]
        overflow = max(0, count - 4)
        assert len(units) - len(groups) == min(count, 4)
        assert len(groups) == math.ceil(overflow / 4)
        assert sum(len(group.members) for group in groups) == overflow

    def test_overflow_past_the_response_cap_joins_the_groups_of_its_site(self) -> None:
        session = _session(_caps(boundaries=3, per_site=4, per_request=2))
        jobs = _queue(session, [A, A, B, B, A, A, B, A])
        a0, a1, b0, b1, a2, a3, b2, a4 = jobs
        assert session.plan().units == (
            a0,
            a1,
            b0,
            Group(B, (b1, b2)),
            Group(A, (a2, a3)),
            Group(A, (a4,)),
        )

    def test_instances_past_the_instance_cap_expire_without_running(self) -> None:
        session = _session(_caps(instances=3, per_site=1, per_request=4))
        jobs = _queue(session, [A, A, A, A, B])
        a0, a1, a2, a3, b0 = jobs
        plan = session.plan()
        assert plan.units == (a0, Group(A, (a1, a2)))
        assert plan.expired == (a3, b0)
        assert session.jobs == tuple(jobs)
        assert (session.instances(A), session.instances(B)) == (4, 1)

    def test_wrappers_and_jobs_share_the_instance_cap_in_document_order(self) -> None:
        session = _session(_caps(instances=2, per_site=1, per_request=4))
        assert session.take_instance() is True
        jobs = _queue(session, [A, B])
        assert session.plan().units == (jobs[0],)
        assert session.plan().expired == (jobs[1],)
        assert session.take_instance() is False

    def test_the_plan_is_a_snapshot(self) -> None:
        session = _session(_caps(per_site=1))
        _queue(session, [A, A])
        before = session.plan()
        _queue(session, [A])
        group = before.units[1]
        assert isinstance(group, Group)
        assert len(group.members) == 1
        assert session.plan() != before


class TestNarrowed:
    """``BoundarySession.narrowed`` keeps the facts and queues a subset afresh."""

    def test_the_kept_jobs_queue_again_under_the_same_facts(self) -> None:
        session = _session(_caps(per_site=1))
        session.facts = ForegroundFacts(Server.ASGI)
        session.threads = (Captured(object(), "state"),)
        first, _, third = _queue(session, [A, A, B])
        narrowed = session.narrowed([first, third])
        assert narrowed is not session
        assert (narrowed.seal, narrowed.deadline) == (session.seal, session.deadline)
        assert narrowed.facts is session.facts
        assert narrowed.threads is session.threads
        assert narrowed.plan().units == (first, third)


class TestWireIds:
    """The wire id format lives in one place, its pattern derived from the seal size."""

    def test_an_id_round_trips_its_seal_and_matches_the_pattern(self) -> None:
        seal = new_seal()
        ident = wire_id(seal, 12)
        assert ident == f"b-{seal}-12"
        assert seal_of(ident) == seal
        assert IDENT.fullmatch(ident) is not None

    @pytest.mark.parametrize(
        "ident", ["b-0badcafe", "b-0BADCAFE-1", "b-0badcaf-1", "b-0badcafe-1234567890"]
    )
    def test_the_pattern_refuses_a_malformed_id(self, ident: str) -> None:
        assert IDENT.fullmatch(ident) is None
