from collections.abc import Callable, Sequence
from functools import partial
from typing import Final

import pytest

from next.boundaries.failures import ErrorKind
from next.boundaries.options import DeferMode
from next.defer import Admit, Capacity, Job, QueueDelay, ServerKind
from next.defer.coordinator import (
    Abandon,
    Command,
    Emit,
    End,
    Finish,
    Machine,
    ProtocolError,
    RunPool,
    RunRequest,
    Totals,
    Unit,
    Wake,
)
from next.defer.errors import BoundaryDeadlineError, BoundaryShedError, InvariantError
from next.defer.markers import (
    Addressing,
    BoundarySite,
    Caps,
    Group,
    JobPlan,
    Lane,
    LaneChoice,
    LaneFacts,
    LaneRule,
    Outcome,
    PoolVerdict,
    Rendered,
    Timing,
)
from tests.support.jobs import plain_job


SITE = BoundarySite(Addressing.COMPONENT, "row", DeferMode.STREAM, "list.djx")
WSGI = LaneFacts(ServerKind.WSGI_THREADED)
ASGI = LaneFacts(ServerKind.ASGI)
ATOMIC_WSGI = LaneFacts(ServerKind.WSGI_THREADED, atomic=("default",))
ATOMIC_ASGI = LaneFacts(ServerKind.ASGI, atomic=("default",))
CAPACITY = Capacity(16, 0, 0, 0.0)

type Event = Callable[[Machine], list[Command]]


_job = partial(plain_job, site=SITE)


def _ok(*nested: Job) -> Rendered:
    return Rendered("<p>ready</p>", "", nested=nested)


class _Admission:
    def __init__(self, *verdicts: Admit) -> None:
        self.verdicts = list(verdicts) or [Admit.POOL]
        self.calls: list[Capacity] = []

    def admit(self, capacity: Capacity) -> Admit:
        self.calls.append(capacity)
        if len(self.verdicts) > 1:
            return self.verdicts.pop(0)
        return self.verdicts[0]


def _caps(*, per_request: int = 2, depth: int = 2, grace: float = 2.0) -> Caps:
    return Caps(10.0, grace, 64, 4, depth, 1000, per_request)


def _machine(
    units: Sequence[Job | Group] | JobPlan,
    *,
    caps: Caps | None = None,
    facts: LaneFacts = WSGI,
    admission: _Admission | None = None,
    deadline: float = 10.0,
    coalesce: float = 0.002,
) -> Machine:
    return Machine(
        units if isinstance(units, JobPlan) else JobPlan(tuple(units)),
        caps or _caps(),
        facts,
        admission or _Admission(),
        deadline=deadline,
        coalesce=coalesce,
    )


def _pool(commands: list[Command]) -> list[str]:
    return [command.unit.id for command in commands if isinstance(command, RunPool)]


def _request(commands: list[Command]) -> list[list[str]]:
    return [
        [unit.id for unit in command.units]
        for command in commands
        if isinstance(command, RunRequest)
    ]


def _emits(commands: list[Command]) -> list[Emit]:
    return [command for command in commands if isinstance(command, Emit)]


def _emitted(commands: list[Command]) -> list[list[str]]:
    return [[item.id for item in emit.items] for emit in _emits(commands)]


def _finish(commands: list[Command]) -> Finish:
    (finish,) = [command for command in commands if isinstance(command, Finish)]
    return finish


class TestPoolFairness:
    """Tests for ``Machine`` pool placement under ``PER_REQUEST``."""

    def test_units_past_per_request_queue_in_document_order(self) -> None:
        machine = _machine([_job("j0"), _job("j1"), _job("j2")])
        commands = machine.start(CAPACITY, 0.0)
        assert _pool(commands) == ["j0", "j1"]
        assert commands[-1] == Wake(10.0)
        later = machine.finished("j0", _ok(), Timing(), 0.5)
        assert _pool(later) == ["j2"]
        assert _emitted(later) == [["j0"]]

    def test_a_unit_carries_its_placement_and_the_earlier_deadline(self) -> None:
        machine = _machine([_job("j0", deadline=20.0)], deadline=8.0)
        (command,) = [c for c in machine.start(CAPACITY, 0.0) if isinstance(c, RunPool)]
        assert command.unit.placement == LaneChoice(PoolVerdict.POOL)
        assert [task.deadline for task in command.unit.tasks] == [8.0]

    def test_a_job_past_its_deadline_in_the_queue_never_runs(self) -> None:
        machine = _machine(
            [_job("j0"), _job("j1", deadline=0.5)], caps=_caps(per_request=1)
        )
        machine.start(CAPACITY, 0.0)
        (emit,) = _emits(machine.tick(0.5))
        assert emit.items == (
            Outcome(
                _job("j1", deadline=0.5),
                timing=Timing(placement=LaneChoice(PoolVerdict.POOL)),
                settled=ErrorKind.TIMEOUT,
            ),
        )
        assert machine.totals == Totals(timeouts=1)
        assert _pool(machine.finished("j0", _ok(), Timing(), 1.0)) == []

    def test_a_late_clock_reading_never_moves_time_back(self) -> None:
        jobs = [_job(f"j{index}") for index in range(3)]
        machine = _machine(jobs, caps=_caps(per_request=3), coalesce=0.25)
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(), Timing(), 0.1)
        machine.written(0.1)
        machine.capacity(CAPACITY, 3.5)
        assert machine.finished("j1", _ok(), Timing(), 1.0)[-1] == Wake(3.75)

    def test_units_behind_the_own_credit_wait_for_it_and_not_the_request_thread(
        self,
    ) -> None:
        machine = _machine(
            [_job(f"j{index}") for index in range(8)],
            caps=_caps(per_request=4),
            admission=QueueDelay(),
        )
        assert _pool(machine.start(CAPACITY, 0.0)) == ["j0", "j1", "j2", "j3"]
        busy = Capacity(16, 4, 0, 0.0)
        assert _request(machine.capacity(busy, 0.3)) == []
        later = machine.finished("j0", _ok(), Timing(), 0.3)
        assert _pool(later) == ["j4"]
        assert _request(later) == []

    def test_a_lagging_pool_hands_units_behind_the_credit_to_the_request_thread(
        self,
    ) -> None:
        machine = _machine(
            [_job("j0"), _job("j1")], caps=_caps(per_request=1), admission=QueueDelay()
        )
        assert _pool(machine.start(CAPACITY, 0.0)) == ["j0"]
        assert _request(machine.capacity(Capacity(16, 16, 9, 0.06), 0.01)) == [["j1"]]


class TestRequestLane:
    """Tests for ``Machine`` on the request lane."""

    def test_a_rule_runs_units_one_at_a_time_in_order(self) -> None:
        machine = _machine([_job("j0"), _job("j1")], facts=ATOMIC_WSGI)
        commands = machine.start(CAPACITY, 0.0)
        (command,) = [c for c in commands if isinstance(c, RunRequest)]
        assert [unit.id for unit in command.units] == ["j0"]
        assert command.units[0].placement == LaneChoice(LaneRule.ATOMIC)
        assert _pool(commands) == []
        later = machine.finished("j0", _ok(), Timing(), 0.1)
        assert _request(later) == [["j1"]]
        assert _emitted(later) == [["j0"]]

    def test_asgi_sends_every_waiting_unit_in_one_hop(self) -> None:
        machine = _machine([_job("j0"), _job("j1"), _job("j2")], facts=ATOMIC_ASGI)
        assert _request(machine.start(CAPACITY, 0.0)) == [["j0", "j1", "j2"]]

    def test_a_lone_job_takes_the_pool(self) -> None:
        machine = _machine([_job("j0")])
        (command,) = [c for c in machine.start(CAPACITY, 0.0) if isinstance(c, RunPool)]
        assert command.unit.placement == LaneChoice(PoolVerdict.POOL)

    def test_a_request_job_past_its_deadline_before_it_starts_never_runs(self) -> None:
        machine = _machine([_job("j0"), _job("j1", deadline=0.5)], facts=ATOMIC_WSGI)
        machine.start(CAPACITY, 0.0)
        (emit,) = _emits(machine.tick(0.5))
        assert [(item.id, item.kind) for item in emit.items] == [
            ("j1", ErrorKind.TIMEOUT)
        ]
        assert _request(machine.finished("j0", _ok(), Timing(), 1.0)) == []


class TestAdmission:
    """Tests for ``Machine`` admission of pool-placed units."""

    def test_a_shed_unit_settles_overloaded_without_running(self) -> None:
        machine = _machine([_job("j0")], admission=_Admission(Admit.SHED))
        commands = machine.start(CAPACITY, 0.0)
        assert _pool(commands) == []
        (emit,) = _emits(commands)
        assert emit.first
        (item,) = emit.items
        assert item.rendered is None
        assert item.kind is ErrorKind.OVERLOADED
        assert machine.totals == Totals(shed=1)
        finish = _finish(machine.written(0.1))
        assert finish == Finish(aborted=False, cut=False, totals=Totals(shed=1))
        assert machine.closed

    def test_request_hands_a_pool_unit_to_the_request_thread(self) -> None:
        admission = _Admission(Admit.REQUEST)
        machine = _machine([_job("j0"), _job("j1")], admission=admission)
        commands = machine.start(CAPACITY, 0.0)
        assert _pool(commands) == []
        (command,) = [c for c in commands if isinstance(c, RunRequest)]
        assert [unit.id for unit in command.units] == ["j0"]
        assert command.units[0].placement == LaneChoice(PoolVerdict.POOL)
        assert admission.calls == [CAPACITY, CAPACITY]

    def test_a_reading_admits_queued_units_again(self) -> None:
        admission = _Admission(Admit.POOL, Admit.POOL, Admit.REQUEST)
        machine = _machine(
            [_job("j0"), _job("j1")], caps=_caps(per_request=1), admission=admission
        )
        assert _pool(machine.start(CAPACITY, 0.0)) == ["j0"]
        lagging = Capacity(16, 16, 4, 0.08)
        assert _request(machine.capacity(CAPACITY, 0.03)) == []
        assert _request(machine.capacity(lagging, 0.08)) == [["j1"]]
        assert admission.calls[-1] == lagging

    @pytest.mark.parametrize("queued", [1, 16, 200])
    def test_a_reading_behind_the_credit_admits_one_queued_unit(
        self, queued: int
    ) -> None:
        admission = _Admission()
        jobs = [_job(f"j{index}") for index in range(queued + 4)]
        machine = _machine(jobs, caps=_caps(per_request=4), admission=admission)
        assert _pool(machine.start(CAPACITY, 0.0)) == ["j0", "j1", "j2", "j3"]
        steps = []
        for now in (0.01, 0.02, 0.03):
            admission.calls.clear()
            assert _pool(machine.capacity(CAPACITY, now)) == []
            steps.append(len(admission.calls))
        assert steps == [1, 1, 1]
        assert _pool(machine.finished("j0", _ok(), Timing(), 0.04)) == ["j4"]

    def test_a_reading_may_shed_a_queued_unit(self) -> None:
        admission = _Admission(Admit.POOL, Admit.SHED)
        machine = _machine(
            [_job("j0"), _job("j1")], caps=_caps(per_request=1), admission=admission
        )
        machine.start(CAPACITY, 0.0)
        (emit,) = _emits(machine.capacity(CAPACITY, 0.1))
        assert [(item.id, item.kind) for item in emit.items] == [
            ("j1", ErrorKind.OVERLOADED)
        ]
        assert machine.totals == Totals(shed=1)


class TestRefused:
    """Tests for ``Machine.refused``."""

    def test_a_refused_unit_behind_the_credit_stays_off_the_request_lane(self) -> None:
        machine = _machine(
            [_job("j0"), _job("j1")],
            caps=_caps(per_request=1),
            facts=ASGI,
            admission=_Admission(Admit.POOL, Admit.REQUEST),
        )
        assert _pool(machine.start(CAPACITY, 0.0)) == ["j0"]
        assert _request(machine.capacity(CAPACITY, 0.005)) == [["j1"]]
        assert _pool(machine.refused("j1", 0.01)) == []
        assert _request(machine.capacity(CAPACITY, 0.02)) == []
        assert _pool(machine.finished("j0", _ok(), Timing(), 0.03)) == ["j1"]

    def test_a_unit_without_a_permit_goes_to_the_pool(self) -> None:
        machine = _machine(
            [_job("j0")], facts=ASGI, admission=_Admission(Admit.REQUEST)
        )
        assert _request(machine.start(CAPACITY, 0.0)) == [["j0"]]
        (command,) = [c for c in machine.refused("j0", 0.01) if isinstance(c, RunPool)]
        assert command.unit.placement == LaneChoice(PoolVerdict.REQUEST_FULL)
        assert _emitted(machine.finished("j0", _ok(), Timing(), 0.1)) == [["j0"]]

    def test_a_forced_unit_waits_for_its_permit(self) -> None:
        machine = _machine([_job("j0")], facts=ATOMIC_ASGI)
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.refused("j0", 0.1)

    @pytest.mark.parametrize("unit_id", ["j0", "missing"])
    def test_a_unit_off_the_request_lane_is_refused(self, unit_id: str) -> None:
        machine = _machine([_job("j0")], facts=ASGI)
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.refused(unit_id, 0.1)

    def test_a_unit_that_started_is_refused(self) -> None:
        group = Group(SITE, (_job("m0"), _job("m1")))
        machine = _machine([group], facts=ASGI, admission=_Admission(Admit.REQUEST))
        assert _request(machine.start(CAPACITY, 0.0)) == [["m0"]]
        machine.finished("m0", _ok(), Timing(), 0.1)
        with pytest.raises(ProtocolError):
            machine.refused("m0", 0.2)


class TestFull:
    """Tests for ``Machine.full``."""

    def test_a_unit_the_full_queue_refused_goes_to_the_request_lane(self) -> None:
        machine = _machine([_job("j0"), _job("j1")], facts=ASGI)
        assert _pool(machine.start(CAPACITY, 0.0)) == ["j0", "j1"]
        commands = machine.full("j0", 0.01)
        (command,) = [c for c in commands if isinstance(c, RunRequest)]
        assert [unit.id for unit in command.units] == ["j0"]
        assert _pool(commands) == []
        (emit,) = _emits(machine.finished("j0", _ok(), Timing(), 0.1))
        assert emit.items == (
            Outcome(
                _job("j0"), _ok(), Timing(Lane.REQUEST, LaneChoice(PoolVerdict.POOL))
            ),
        )

    def test_a_unit_the_request_lane_refused_too_is_shed_once(self) -> None:
        machine = _machine(
            [_job("j0")], facts=ASGI, admission=_Admission(Admit.REQUEST)
        )
        assert _request(machine.start(CAPACITY, 0.0)) == [["j0"]]
        assert _pool(machine.refused("j0", 0.01)) == ["j0"]
        commands = machine.full("j0", 0.02)
        assert _pool(commands) == []
        assert _request(commands) == []
        (emit,) = _emits(commands)
        (item,) = emit.items
        assert item.rendered is None
        assert item.kind is ErrorKind.OVERLOADED
        assert machine.totals == Totals(shed=1)

    @pytest.mark.parametrize("unit_id", ["j0", "missing"])
    def test_a_unit_not_in_the_pool_is_refused(self, unit_id: str) -> None:
        machine = _machine([_job("j0")], facts=ATOMIC_ASGI)
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.full(unit_id, 0.1)

    def test_a_unit_that_reported_is_refused(self) -> None:
        group = Group(SITE, (_job("m0"), _job("m1")))
        machine = _machine([group])
        assert _pool(machine.start(CAPACITY, 0.0)) == ["m0"]
        machine.finished("m0", _ok(), Timing(), 0.1)
        with pytest.raises(ProtocolError):
            machine.full("m0", 0.2)


class TestDeadlines:
    """Tests for ``Machine`` timeouts, late units and the response deadline."""

    def test_a_running_unit_past_its_deadline_is_late(self) -> None:
        machine = _machine(
            [_job("j0", deadline=1.0), _job("j1")], caps=_caps(per_request=1)
        )
        machine.start(CAPACITY, 0.0)
        commands = machine.tick(1.0)
        (emit,) = _emits(commands)
        (item,) = emit.items
        assert item == Outcome(
            _job("j0", deadline=1.0),
            timing=Timing(Lane.POOL, LaneChoice(PoolVerdict.POOL)),
            settled=ErrorKind.TIMEOUT,
        )
        assert _pool(commands) == []
        assert machine.totals == Totals(late=1, timeouts=1)
        assert _pool(machine.finished("j0", _ok(), Timing(), 1.5)) == ["j1"]

    def test_a_result_reported_past_the_deadline_is_dropped(self) -> None:
        machine = _machine([_job("j0", deadline=1.0)])
        machine.start(CAPACITY, 0.0)
        (emit,) = _emits(machine.finished("j0", _ok(), Timing(), 1.2))
        assert emit.items[0].rendered is None
        assert machine.totals.late == 1

    def test_the_deadline_settles_everything_and_the_grace_cuts(self) -> None:
        machine = _machine([_job("j0"), _job("j1")], deadline=4.0)
        assert machine.start(CAPACITY, 0.0)[-1] == Wake(4.0)
        assert _emitted(machine.finished("j0", _ok(), Timing(), 1.0)) == [["j0"]]
        assert machine.tick(4.0) == [Wake(6.0)]
        assert machine.totals == Totals(late=1, timeouts=1)
        finish = _finish(machine.tick(6.0))
        assert finish == Finish(
            aborted=False, cut=True, totals=Totals(late=1, timeouts=1, dropped=1)
        )
        assert machine.written(6.5) == []

    @pytest.mark.parametrize(
        "event",
        [
            lambda machine: machine.capacity(CAPACITY, 20.0),
            lambda machine: machine.finished("j1", _ok(), Timing(), 20.0),
            lambda machine: machine.written(20.0),
            lambda machine: machine.tick(20.0),
        ],
    )
    def test_any_event_past_the_grace_cuts(self, event: Event) -> None:
        machine = _machine([_job("j0"), _job("j1")])
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(), Timing(), 0.1)
        finish = _finish(event(machine))
        assert finish.cut
        assert finish.totals == Totals(late=1, timeouts=1, dropped=1)

    def test_a_nested_job_found_past_the_deadline_settles_at_once(self) -> None:
        child = _job("j0.0", parent="j0")
        machine = _machine([_job("j0")], deadline=4.0)
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(child), Timing(), 3.0)
        commands = machine.written(4.5)
        assert _pool(commands) == []
        (emit,) = _emits(commands)
        assert [(item.id, item.kind) for item in emit.items] == [
            ("j0.0", ErrorKind.TIMEOUT)
        ]

    def test_jobs_over_the_instance_cap_settle_at_start(self) -> None:
        machine = _machine(JobPlan((_job("j0"),), (_job("j1"),)))
        commands = machine.start(CAPACITY, 0.0)
        assert _pool(commands) == ["j0"]
        (emit,) = _emits(commands)
        assert emit.items == (Outcome(_job("j1"), settled=ErrorKind.TIMEOUT),)
        assert machine.totals == Totals(timeouts=1)


class TestGroups:
    """Tests for ``Machine`` group units."""

    def test_members_time_out_midway_and_write_as_one_batch(self) -> None:
        members = (
            _job("m0", deadline=1.0),
            _job("m1", deadline=2.0),
            _job("m2", deadline=3.0),
        )
        machine = _machine([Group(SITE, members)])
        (command,) = [c for c in machine.start(CAPACITY, 0.0) if isinstance(c, RunPool)]
        assert [task.deadline for task in command.unit.tasks] == [1.0, 2.0, 3.0]
        assert _emits(machine.finished("m0", _ok(), Timing(), 0.5)) == []
        assert _emits(machine.tick(2.0)) == []
        assert _emits(machine.finished("m1", _ok(), Timing(), 2.1)) == []
        (emit,) = _emits(machine.finished("m2", _ok(), Timing(), 2.5))
        assert [(item.id, item.kind) for item in emit.items] == [
            ("m0", None),
            ("m1", ErrorKind.TIMEOUT),
            ("m2", None),
        ]

    def test_a_member_past_its_deadline_before_dispatch_is_left_out(self) -> None:
        group = Group(SITE, (_job("m0", deadline=0.5), _job("m1")))
        machine = _machine([_job("j0"), group], caps=_caps(per_request=1))
        machine.start(CAPACITY, 0.0)
        machine.tick(0.5)
        commands = machine.finished("j0", _ok(), Timing(), 1.0)
        (command,) = [c for c in commands if isinstance(c, RunPool)]
        assert [task.job.id for task in command.unit.tasks] == ["m1"]


class TestCoalescing:
    """Tests for ``Machine`` outcome batches."""

    def test_the_first_chunk_is_never_delayed(self) -> None:
        machine = _machine([_job("j0"), _job("j1")])
        machine.start(CAPACITY, 0.0)
        (emit,) = _emits(machine.finished("j0", _ok(), Timing(), 0.1))
        assert emit.first

    def test_outcomes_ready_within_the_window_are_one_batch(self) -> None:
        machine = _machine(
            [_job("j0"), _job("j1"), _job("j2"), _job("j3")], caps=_caps(per_request=4)
        )
        machine.start(CAPACITY, 0.0)
        assert _emitted(machine.finished("j0", _ok(), Timing(), 0.1)) == [["j0"]]
        assert _emits(machine.finished("j2", _ok(), Timing(), 0.2)) == []
        assert _emits(machine.finished("j1", _ok(), Timing(), 0.2005)) == []
        assert machine.written(0.201) == [Wake(0.2 + 0.002)]
        (emit,) = _emits(machine.tick(0.2 + 0.002))
        assert [item.id for item in emit.items] == ["j1", "j2"]
        assert not emit.first

    def test_nothing_left_to_wait_for_writes_at_once(self) -> None:
        machine = _machine([_job("j0"), _job("j1")], coalesce=1.0)
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(), Timing(), 0.1)
        machine.finished("j1", _ok(), Timing(), 0.15)
        assert _emitted(machine.written(0.16)) == [["j1"]]

    def test_an_unchanged_wake_is_not_repeated(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        assert machine.tick(1.0) == []


class TestNested:
    """Tests for ``Machine`` nested jobs."""

    def test_children_run_only_after_the_parent_chunk_is_written(self) -> None:
        child = _job("j0.0", parent="j0")
        machine = _machine([_job("j0"), _job("j1")], caps=_caps(per_request=4))
        machine.start(CAPACITY, 0.0)
        commands = machine.finished("j0", _ok(child), Timing(), 0.1)
        assert _pool(commands) == []
        assert _pool(machine.written(0.2)) == ["j0.0"]
        assert _emits(machine.finished("j0.0", _ok(), Timing(), 0.3)) == []
        assert _emitted(machine.finished("j1", _ok(), Timing(), 0.301)) == [
            ["j0.0", "j1"]
        ]

    @pytest.mark.parametrize(
        "child", [_job("j9.0", parent="j9"), _job("j0.0", parent="j0", depth=3)]
    )
    def test_a_foreign_or_too_deep_child_is_refused(self, child: Job) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.finished("j0", _ok(child), Timing(), 0.1)


class TestResults:
    """Tests for ``Machine.finished`` results."""

    @pytest.mark.parametrize(
        ("error", "kind", "totals"),
        [
            (BoundaryShedError(), ErrorKind.OVERLOADED, Totals(shed=1)),
            (BoundaryDeadlineError(), ErrorKind.TIMEOUT, Totals(timeouts=1)),
            (ValueError("boom"), ErrorKind.ERROR, Totals()),
        ],
    )
    def test_a_raised_error_settles_by_its_kind(
        self, error: Exception, kind: ErrorKind, totals: Totals
    ) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        (emit,) = _emits(machine.finished("j0", error, Timing(), 0.1))
        (item,) = emit.items
        assert item.rendered is None
        assert (item.kind, item.error, machine.totals) == (kind, error, totals)
        assert item.timing == Timing(Lane.POOL, LaneChoice(PoolVerdict.POOL))

    def test_a_rendered_error_ends_by_its_kind(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        rendered = Rendered("<p>slow</p>", "", kind=ErrorKind.TIMEOUT)
        machine.finished("j0", rendered, Timing(), 0.1)
        assert machine.totals == Totals(timeouts=1)

    def test_the_driver_timing_is_kept(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        timing = Timing(Lane.REQUEST, LaneChoice(PoolVerdict.REQUEST_FULL), 0.1, 0.2)
        (emit,) = _emits(machine.finished("j0", _ok(), timing, 0.3))
        assert emit.items == (Outcome(_job("j0"), _ok(), timing),)


class TestCancel:
    """Tests for ``Machine.cancel``."""

    def test_before_start_every_job_is_cancelled(self) -> None:
        machine = _machine([_job("j0")])
        assert machine.cancel(0.0) == [
            Finish(aborted=True, cut=False, totals=Totals(cancelled=1))
        ]
        assert machine.start(CAPACITY, 0.0) == []

    def test_running_units_are_abandoned_and_queued_ones_cancelled(self) -> None:
        machine = _machine([_job("j0"), _job("j1")], caps=_caps(per_request=1))
        machine.start(CAPACITY, 0.0)
        commands = machine.cancel(0.5)
        assert commands[0] == Abandon(("j0",))
        assert _finish(commands).totals == Totals(abandoned=1, cancelled=1)

    def test_while_writing_the_waiting_outcomes_are_dropped(self) -> None:
        machine = _machine([_job("j0"), _job("j1")])
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(), Timing(), 0.1)
        machine.finished("j1", _ok(), Timing(), 0.2)
        assert machine.cancel(0.3) == [
            Finish(aborted=True, cut=False, totals=Totals(dropped=1))
        ]

    def test_after_finish_nothing_is_issued(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(), Timing(), 0.1)
        assert _finish(machine.written(0.2)) == Finish(
            aborted=False, cut=False, totals=Totals()
        )
        assert machine.cancel(0.3) == []
        assert machine.capacity(CAPACITY, 0.35) == []
        assert machine.tick(0.4) == []
        assert machine.refused("j0", 0.5) == []
        assert machine.full("j0", 0.55) == []
        assert machine.finished("j0", _ok(), Timing(), 0.6) == []


class TestProtocol:
    """Tests for ``Machine`` refusing events out of protocol."""

    def test_an_event_before_start_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            _machine([_job("j0")]).tick(0.0)

    def test_a_second_start_is_refused(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.start(CAPACITY, 0.1)

    def test_a_job_queued_twice_is_refused(self) -> None:
        with pytest.raises(ProtocolError):
            _machine([_job("j0"), _job("j0")])

    @pytest.mark.parametrize("job_id", ["missing", "j1"])
    def test_a_result_of_no_running_job_is_refused(self, job_id: str) -> None:
        machine = _machine([_job("j0"), _job("j1")], caps=_caps(per_request=1))
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.finished(job_id, _ok(), Timing(), 0.1)

    def test_a_second_result_is_refused(self) -> None:
        machine = _machine([_job("j0"), _job("j1")])
        machine.start(CAPACITY, 0.0)
        machine.finished("j0", _ok(), Timing(), 0.1)
        with pytest.raises(ProtocolError):
            machine.finished("j0", _ok(), Timing(), 0.2)

    def test_a_write_with_no_batch_out_is_refused(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        with pytest.raises(ProtocolError):
            machine.written(0.1)


class TestInvariants:
    """``Machine`` raises ``InvariantError`` once its own bookkeeping breaks."""

    def test_a_second_terminal_state_is_refused(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        entry = machine._entries["j0"]
        entry.end = End.RAN
        with pytest.raises(InvariantError):
            machine._end(entry, End.FAILED, None, 0.1)

    def test_a_second_command_of_a_running_job_is_refused(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        unit = machine._entries["j0"].unit
        assert unit is not None
        with pytest.raises(InvariantError):
            machine._dispatch(unit)

    def test_a_second_emit_of_an_outcome_is_refused(self) -> None:
        machine = _machine([_job("j0")])
        machine.start(CAPACITY, 0.0)
        machine._emitted.add("j0")
        with pytest.raises(InvariantError):
            machine.finished("j0", _ok(), Timing(), 0.1)

    def test_a_command_after_finish_is_refused(self) -> None:
        machine = _machine([])
        machine.start(CAPACITY, 0.0)
        assert machine.closed
        with pytest.raises(InvariantError):
            machine._command(Wake(1.0))


_MASK: Final = (1 << 64) - 1


class _Dice:
    """A seeded splitmix64 stream that draws alike on every supported Python version."""

    def __init__(self, seed: int) -> None:
        self.state = seed

    def random(self) -> float:
        self.state = (self.state + 0x9E3779B97F4A7C15) & _MASK
        mixed = ((self.state ^ (self.state >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        mixed = ((mixed ^ (mixed >> 27)) * 0x94D049BB133111EB) & _MASK
        return ((mixed ^ (mixed >> 31)) >> 11) / (1 << 53)

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.random()

    def randint(self, low: int, high: int) -> int:
        return low + int(self.random() * (high - low + 1))

    def choice[T](self, options: Sequence[T]) -> T:
        return options[int(self.random() * len(options))]

    def verdict(self) -> Admit:
        roll = self.random()
        if roll < 1 / 8:
            return Admit.SHED
        return Admit.REQUEST if roll < 2 / 8 else Admit.POOL


class _RandomDriver:
    def __init__(self, seed: int) -> None:
        self.rng = _Dice(seed)
        rng = self.rng
        self.caps = Caps(10.0, 1.0, 64, 4, 2, 1000, rng.randint(1, 3))
        self.jobs: dict[str, Job] = {}
        units: list[Job | Group] = []
        for index in range(rng.randint(1, 6)):
            members = tuple(
                self._add(_job(f"u{index}m{member}", deadline=rng.uniform(0.5, 12.0)))
                for member in range(rng.choice((1, 1, 2, 3)))
            )
            units.append(members[0] if len(members) == 1 else Group(SITE, members))
        expired = tuple(self._add(_job(f"x{n}")) for n in range(rng.randint(0, 1)))
        self.known = set(self.jobs)
        self.machine = _machine(
            JobPlan(tuple(units), expired),
            caps=self.caps,
            facts=rng.choice([WSGI, ASGI, ATOMIC_WSGI, ATOMIC_ASGI]),
            admission=_Admission(*(rng.verdict() for _ in range(50))),
        )
        self.slow = rng.choice((1.0, 0.05))
        self.running: dict[str, str] = {}
        self.refusable: set[str] = set()
        self.writing: list[Emit] = []
        self.emitted: dict[str, Outcome] = {}
        self.written: set[str] = set()
        self.finishes: list[Finish] = []

    def _add(self, job: Job) -> Job:
        self.jobs[job.id] = job
        return job

    def apply(self, commands: list[Command]) -> None:
        for command in commands:
            assert not self.finishes
            if isinstance(command, RunPool):
                self._track(command.unit)
            elif isinstance(command, RunRequest):
                for unit in command.units:
                    self._track(unit)
                    if not unit.placement.forced:
                        self.refusable.add(unit.id)
            elif isinstance(command, Abandon):
                self._drop(set(command.ids))
            elif isinstance(command, Emit):
                self._emit(command)
            elif isinstance(command, Finish):
                self.finishes.append(command)

    def _track(self, unit: Unit) -> None:
        for task in unit.tasks:
            assert task.job.id not in self.running
            self.running[task.job.id] = unit.id

    def _drop(self, unit_ids: set[str]) -> None:
        for job_id, unit_id in list(self.running.items()):
            if unit_id in unit_ids:
                del self.running[job_id]

    def _emit(self, emit: Emit) -> None:
        for item in emit.items:
            assert item.id not in self.emitted
            parent = self.jobs[item.id].parent
            assert parent is None or parent in self.written
            self.emitted[item.id] = item
        assert not self.writing
        self.writing.append(emit)

    def _result(self, job: Job) -> Rendered | Exception:
        pick = self.rng.random()
        if pick < 0.1:
            return BoundaryDeadlineError()
        if pick < 0.2:
            return ValueError("boom")
        if pick < 0.5 and job.depth < self.caps.depth:
            child = _job(
                f"{job.id}.n",
                deadline=self.rng.uniform(0.5, 12.0),
                parent=job.id,
                depth=job.depth + 1,
            )
            return _ok(self._add(child))
        return _ok()

    def step(self, now: float) -> None:
        rng, machine = self.rng, self.machine
        roll = rng.random()
        if roll < 0.02:
            self.apply(machine.cancel(now))
        elif roll < 0.45 and self.running:
            job_id = rng.choice(sorted(self.running))
            self.refusable.discard(self.running.pop(job_id))
            result = self._result(self.jobs[job_id])
            self.apply(machine.finished(job_id, result, Timing(), now))
        elif roll < 0.75 and self.writing and rng.random() < self.slow:
            emit = self.writing.pop(0)
            self.written.update(item.id for item in emit.items)
            self.apply(machine.written(now))
            for item in emit.items:
                if not self.finishes and item.rendered is not None:
                    self.known.update(job.id for job in item.rendered.nested)
        elif roll < 0.8 and self.refusable:
            unit_id = rng.choice(sorted(self.refusable))
            self.refusable.discard(unit_id)
            self._drop({unit_id})
            self.apply(machine.refused(unit_id, now))
        elif roll < 0.88:
            self.apply(machine.capacity(Capacity(16, 4, 2, 0.01), now))
        else:
            self.apply(machine.tick(now))

    def run(self) -> None:
        now = 0.0
        self.apply(self.machine.start(CAPACITY, now))
        for _ in range(400):
            if self.finishes:
                break
            now += self.rng.uniform(0.0, 0.3)
            self.step(now)
        if not self.finishes:
            self.apply(self.machine.tick(now + 100.0))


@pytest.mark.parametrize("seed", range(200))
def test_random_event_orders_keep_the_invariants(seed: int) -> None:
    driver = _RandomDriver(seed)
    driver.run()
    (finish,) = driver.finishes
    machine = driver.machine
    assert machine.closed
    if not finish.aborted:
        assert len(driver.emitted) + finish.totals.dropped == len(driver.known)
    if not (finish.aborted or finish.cut):
        assert set(driver.emitted) == driver.known
        assert not driver.writing
