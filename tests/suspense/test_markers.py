import dataclasses
from dataclasses import dataclass
from functools import partial

import pytest

from next.defer import BoundaryOptions, DeferMode, ErrorKind
from next.suspense import Capacity, Job, Lane, Placement, Rule, Server, State, Verdict
from next.suspense.markers import (
    Addressing,
    Caps,
    Effects,
    ForegroundFacts,
    Group,
    Outcome,
    Rendered,
    Site,
    Timing,
)
from next.testing import override_next_settings
from tests.support.jobs import plain_job


SITE = Site(Addressing.COMPONENT, "card", DeferMode.STREAM, "shop/list.djx", 2)
OTHER = Site(Addressing.COMPONENT, "card", DeferMode.STREAM, "shop/list.djx", 5)
PAGE = Site(Addressing.PAGE, "/shop/", DeferMode.STREAM, "shop/page.py")


def _fallback(kind: ErrorKind) -> Rendered[str]:
    return Rendered("<p>failed</p>", "", kind=kind)


def _render() -> Rendered[str]:
    return Rendered("<p>ready</p>", "assets")


async def _arender() -> Rendered[str]:
    return _render()


_job = partial(
    plain_job, "b-00000000-0", key="1a2b3c4d", site=SITE, deadline=100.0, delta="assets"
)


class TestPlacement:
    """Tests for ``Placement``."""

    @pytest.mark.parametrize("rule", list(Rule))
    def test_every_rule_forces_the_request_lane(self, rule: Rule) -> None:
        placement = Placement(rule)
        assert placement.lane is Lane.REQUEST
        assert placement.forced

    @pytest.mark.parametrize("verdict", list(Verdict))
    def test_every_verdict_takes_the_pool(self, verdict: Verdict) -> None:
        placement = Placement(verdict)
        assert placement.lane is Lane.POOL
        assert not placement.forced


class TestSite:
    """Tests for ``Site``."""

    def test_a_page_and_a_component_of_one_name_differ(self) -> None:
        assert dataclasses.replace(PAGE, name="card") != SITE

    def test_a_page_site_defaults_to_index_zero(self) -> None:
        assert PAGE.index == 0

    def test_sites_hash_by_value(self) -> None:
        twin = Site(Addressing.COMPONENT, "card", DeferMode.STREAM, "shop/list.djx", 2)
        assert {SITE: 1}[twin] == 1


class TestEffects:
    """Tests for ``Effects``."""

    def test_no_write_reads_false(self) -> None:
        assert not Effects()

    @pytest.mark.parametrize(
        "field",
        ["session_modified", "session_key_changed", "messages_used", "csrf_changed"],
    )
    def test_any_write_reads_true(self, field: str) -> None:
        assert Effects(**{field: True})


class TestRendered:
    """Tests for ``Rendered``."""

    def test_content_without_a_kind_is_ready(self) -> None:
        rendered = _render()
        assert rendered.state is State.READY
        assert rendered.nested == ()
        assert not rendered.degraded
        assert not rendered.effects
        assert rendered.intended is None

    def test_content_with_a_kind_is_an_error(self) -> None:
        assert _fallback(ErrorKind.TIMEOUT).state is State.ERROR

    @pytest.mark.parametrize("kind", [ErrorKind.INCOMPLETE, ErrorKind.NETWORK])
    def test_a_client_kind_is_refused(self, kind: ErrorKind) -> None:
        with pytest.raises(ValueError, match="never settles a boundary as"):
            Rendered("", "", kind=kind)

    def test_nested_jobs_are_carried(self) -> None:
        child = _job(parent="b-00000000-0", depth=1)
        assert Rendered("", "", nested=(child,)).nested == (child,)


class TestJob:
    """Tests for ``Job``."""

    def test_a_sync_job(self) -> None:
        job = _job()
        assert not job.asynchronous
        assert job.addressing is Addressing.COMPONENT
        assert job.run is not None
        assert job.run().html == "<p>ready</p>"

    def test_an_async_job(self) -> None:
        job = Job(
            "b-00000000-1",
            "1a2b3c4d",
            PAGE,
            BoundaryOptions(),
            100.0,
            _fallback,
            arun=_arender,
        )
        assert job.asynchronous
        assert job.addressing is Addressing.PAGE

    def test_a_job_without_a_body_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly one of run and arun"):
            Job("b-0-0", "k", SITE, BoundaryOptions(), 1.0, _fallback)

    def test_a_job_with_both_bodies_is_refused(self) -> None:
        with pytest.raises(ValueError, match="exactly one of run and arun"):
            Job(
                "b-0-0",
                "k",
                SITE,
                BoundaryOptions(),
                1.0,
                _fallback,
                run=_render,
                arun=_arender,
            )

    @pytest.mark.parametrize(
        ("parent", "depth"), [(None, 1), ("b-0-0", 0), ("b-0-0", -1), (None, -1)]
    )
    def test_a_parent_and_a_depth_that_disagree_are_refused(
        self, parent: str | None, depth: int
    ) -> None:
        with pytest.raises(ValueError, match="parent exactly when its depth"):
            _job(parent=parent, depth=depth)

    def test_a_nested_job_names_its_parent(self) -> None:
        job = _job(parent="b-00000000-0", depth=2)
        assert (job.parent, job.depth) == ("b-00000000-0", 2)

    def test_remaining_counts_down_to_zero(self) -> None:
        job = _job()
        assert job.remaining(99.5) == 0.5
        assert job.remaining(100.0) == 0.0
        assert job.remaining(250.0) == 0.0

    def test_the_fallback_renders_the_error_ui_of_a_kind(self) -> None:
        assert _job().fallback(ErrorKind.OVERLOADED).kind is ErrorKind.OVERLOADED


class TestGroup:
    """Tests for ``Group``."""

    def test_members_of_one_site(self) -> None:
        members = (_job(), _job())
        assert Group(SITE, members).members == members

    def test_an_empty_group_is_refused(self) -> None:
        with pytest.raises(ValueError, match="at least one job"):
            Group(SITE, ())

    def test_a_member_of_another_site_is_refused(self) -> None:
        with pytest.raises(ValueError, match="belongs to the site 'card'"):
            Group(SITE, (_job(), _job(site=OTHER)))


class TestOutcome:
    """Tests for ``Outcome``."""

    def test_a_ready_outcome(self) -> None:
        outcome = Outcome("b-00000000-0", _render())
        assert outcome.state is State.READY
        assert outcome.kind is None
        assert outcome.timing == Timing()

    def test_an_error_outcome_with_its_timing(self) -> None:
        timing = Timing(Lane.POOL, Placement(Verdict.POOL), 0.01, 0.2)
        outcome = Outcome("b-00000000-0", _fallback(ErrorKind.TIMEOUT), timing)
        assert outcome.state is State.ERROR
        assert outcome.kind is ErrorKind.TIMEOUT
        assert outcome.timing.lane is Lane.POOL

    def test_a_job_that_never_ran_has_no_lane(self) -> None:
        assert Timing().lane is None
        assert Timing().placement is None


@dataclass(frozen=True, slots=True)
class RuleCase:
    """One set of foreground facts, the opt-out of a job and the rule that holds."""

    id: str
    facts: ForegroundFacts
    parallel: bool
    rule: Rule | None


_PLAIN = ForegroundFacts(Server.WSGI_THREADED)

RULE_CASES: tuple[RuleCase, ...] = (
    RuleCase("none", _PLAIN, True, None),
    RuleCase(
        "atomic", dataclasses.replace(_PLAIN, atomic=("default",)), True, Rule.ATOMIC
    ),
    RuleCase(
        "sqlite-file",
        dataclasses.replace(_PLAIN, sqlite=("default",)),
        True,
        Rule.SQLITE,
    ),
    RuleCase(
        "sqlite-file-parallel",
        dataclasses.replace(_PLAIN, sqlite=("default",), sqlite_parallel=True),
        True,
        None,
    ),
    RuleCase(
        "sqlite-memory-parallel",
        dataclasses.replace(
            _PLAIN, sqlite=("default",), memory=("default",), sqlite_parallel=True
        ),
        True,
        Rule.SQLITE,
    ),
    RuleCase(
        "connection-state",
        dataclasses.replace(_PLAIN, connection_sources=("routers",)),
        True,
        Rule.CONNECTION_STATE,
    ),
    RuleCase(
        "thread-local",
        dataclasses.replace(_PLAIN, thread_sources=("crum",)),
        True,
        Rule.THREAD_LOCAL,
    ),
    RuleCase(
        "global-opt-out",
        dataclasses.replace(_PLAIN, parallel=False),
        True,
        Rule.OPT_OUT,
    ),
    RuleCase("job-opt-out", _PLAIN, False, Rule.OPT_OUT),
    RuleCase(
        "atomic-before-sqlite",
        dataclasses.replace(_PLAIN, atomic=("default",), sqlite=("default",)),
        True,
        Rule.ATOMIC,
    ),
    RuleCase(
        "sqlite-before-connection-state",
        dataclasses.replace(
            _PLAIN, sqlite=("default",), connection_sources=("routers",)
        ),
        True,
        Rule.SQLITE,
    ),
    RuleCase(
        "connection-state-before-thread-local",
        dataclasses.replace(
            _PLAIN, connection_sources=("routers",), thread_sources=("crum",)
        ),
        True,
        Rule.CONNECTION_STATE,
    ),
    RuleCase(
        "thread-local-before-opt-out",
        dataclasses.replace(_PLAIN, thread_sources=("crum",), parallel=False),
        False,
        Rule.THREAD_LOCAL,
    ),
)


class TestForegroundFacts:
    """Tests for ``ForegroundFacts``."""

    @pytest.mark.parametrize("case", RULE_CASES, ids=[case.id for case in RULE_CASES])
    def test_the_first_rule_that_holds_wins(self, case: RuleCase) -> None:
        options = BoundaryOptions(parallel=case.parallel)
        assert case.facts.rule(options) is case.rule

    def test_every_rule_has_a_case(self) -> None:
        assert {case.rule for case in RULE_CASES} >= set(Rule)

    def test_an_in_memory_alias_outside_the_sqlite_aliases_is_refused(self) -> None:
        with pytest.raises(ValueError, match="in-memory alias is a SQLite alias"):
            ForegroundFacts(Server.ASGI, memory=("default",))

    def test_the_defaults_hold_no_rule(self) -> None:
        facts = ForegroundFacts(Server.ASGI)
        assert facts.parallel
        assert not facts.sqlite_parallel
        assert facts.rule(BoundaryOptions()) is None


class TestCapacity:
    """Tests for ``Capacity``."""

    def test_idle_is_the_free_workers(self) -> None:
        assert Capacity(16, 10, 0, 0.0).idle == 6

    def test_idle_never_reads_below_zero(self) -> None:
        assert Capacity(4, 6, 3, 0.2).idle == 0


class TestCaps:
    """Tests for ``Caps``."""

    def test_the_defaults(self) -> None:
        with override_next_settings(DEFER={}):
            assert Caps.from_settings() == Caps(10.0, 2.0, 64, 4, 4, 1000, 4)

    def test_set_values_are_read(self) -> None:
        defer = {
            "DEADLINE": "3s",
            "WRITE_GRACE": "500ms",
            "MAX_BOUNDARIES": 8,
            "MAX_PER_SITE": 2,
            "MAX_DEPTH": 1,
            "MAX_INSTANCES": 50,
            "EXECUTOR": {"OPTIONS": {"PER_REQUEST": 6}},
        }
        with override_next_settings(DEFER=defer):
            assert Caps.from_settings() == Caps(3.0, 0.5, 8, 2, 1, 50, 6)

    @pytest.mark.parametrize(
        "executor",
        [
            "next.suspense.LaneExecutor",
            {"OPTIONS": "fast"},
            {"OPTIONS": {}},
            {"OPTIONS": {"PER_REQUEST": 0}},
            {"OPTIONS": {"PER_REQUEST": True}},
            {"OPTIONS": {"PER_REQUEST": "4"}},
        ],
    )
    def test_a_misfit_per_request_reads_its_default(self, executor: object) -> None:
        with override_next_settings(DEFER={"EXECUTOR": executor}):
            assert Caps.from_settings().per_request == 4

    def test_a_misfit_cap_reads_its_default(self) -> None:
        with override_next_settings(DEFER={"MAX_PER_SITE": 0, "DEADLINE": 0}):
            caps = Caps.from_settings()
        assert (caps.per_site, caps.deadline) == (4, 10.0)
