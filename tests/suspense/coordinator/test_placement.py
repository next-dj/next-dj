import sys
import threading
from functools import partial

import pytest

from next.defer import DeferMode
from next.suspense import Placement, Rule, Server, Verdict
from next.suspense.coordinator import COSTS, Cost, CostTable, Thresholds, place
from next.suspense.coordinator.placement import BAND, WEIGHT, gil_enabled
from next.suspense.markers import Addressing, ForegroundFacts, Site
from next.testing import override_next_settings
from tests.support.jobs import plain_job


SITE = Site(Addressing.COMPONENT, "card", DeferMode.STREAM, "shop/list.djx", 2)
OTHER = Site(Addressing.COMPONENT, "row", DeferMode.STREAM, "shop/list.djx", 3)
KEY = SITE.cost_key
ADAPTIVE = Thresholds("adaptive", 300_000, 0.8, 2_000_000, 3)
FACTS = ForegroundFacts(Server.WSGI_THREADED)


_job = partial(plain_job, site=SITE, key="k")


def _table(wall: int, cpu: int, runs: int) -> CostTable:
    table = CostTable()
    for _ in range(runs):
        table.observe(KEY, wall, cpu)
    return table


class TestCost:
    """Tests for ``Cost``."""

    def test_a_run_moves_each_average_by_the_weight(self) -> None:
        cost = Cost(1000.0, 500.0, 1).then(2000, 1500)
        assert cost == Cost(1000.0 + WEIGHT * 1000, 500.0 + WEIGHT * 1000, 2)

    def test_a_steady_site_keeps_its_averages(self) -> None:
        assert Cost(800.0, 400.0, 5).then(800, 400) == Cost(800.0, 400.0, 6)

    def test_a_run_keeps_the_lane_the_site_leans_to(self) -> None:
        assert Cost(800.0, 400.0, 5, handoff=True).then(800, 400).handoff


class TestCostTable:
    """Tests for ``CostTable``."""

    def test_an_unseen_site_has_no_cost(self) -> None:
        assert CostTable().cost(KEY) is None

    def test_the_first_run_is_taken_as_it_is(self) -> None:
        table = CostTable()
        table.observe(KEY, 1200, 900)
        assert table.cost(KEY) == Cost(1200.0, 900.0, 1)

    def test_later_runs_fold_into_the_averages(self) -> None:
        table = CostTable()
        table.observe(KEY, 1000, 1000)
        table.observe(KEY, 2000, 0)
        assert table.cost(KEY) == Cost(1200.0, 800.0, 2)

    def test_the_oldest_site_is_evicted_past_the_bound(self) -> None:
        table = CostTable(maxsize=2)
        keys = [(Addressing.COMPONENT, name, DeferMode.STREAM) for name in "abc"]
        for key in keys:
            table.observe(key, 10, 10)
        assert len(table) == 2
        assert table.cost(keys[0]) is None
        assert table.cost(keys[2]) == Cost(10.0, 10.0, 1)

    def test_an_evicted_site_starts_over_and_keeps_the_bound(self) -> None:
        table = CostTable(maxsize=1)
        table.observe(KEY, 10, 10)
        table.observe(OTHER.cost_key, 20, 20)
        table.observe(KEY, 30, 30)
        assert len(table) == 1
        assert table.cost(KEY) == Cost(30.0, 30.0, 1)

    def test_a_site_that_keeps_running_is_never_evicted(self) -> None:
        table = CostTable(maxsize=2)
        hot, cold, new = [
            (Addressing.COMPONENT, name, DeferMode.STREAM) for name in "abc"
        ]
        table.observe(hot, 10, 10)
        table.observe(cold, 10, 10)
        table.observe(hot, 10, 10)
        table.observe(new, 10, 10)
        assert table.cost(cold) is None
        assert table.cost(hot) == Cost(10.0, 10.0, 2)
        assert table.cost(new) == Cost(10.0, 10.0, 1)

    def test_leaning_records_the_lane_of_a_known_site_only(self) -> None:
        table = CostTable()
        table.lean(KEY, handoff=True)
        assert table.cost(KEY) is None
        table.observe(KEY, 10, 10)
        table.lean(KEY, handoff=True)
        assert table.cost(KEY) == Cost(10.0, 10.0, 1, handoff=True)

    def test_clear_forgets_every_site(self) -> None:
        table = CostTable()
        table.observe(KEY, 10, 10)
        table.clear()
        assert len(table) == 0
        assert table.cost(KEY) is None

    def test_concurrent_runs_are_all_counted(self) -> None:
        table = CostTable()
        keys = [KEY, OTHER.cost_key]

        def observe() -> None:
            for index in range(200):
                table.observe(keys[index % 2], 100, 50)

        threads = [threading.Thread(target=observe) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sum(cost.runs for cost in map(table.cost, keys) if cost) == 1600

    def test_the_process_table_is_one_instance(self) -> None:
        assert isinstance(COSTS, CostTable)


class TestThresholds:
    """Tests for ``Thresholds.from_settings``."""

    def test_the_defaults_are_read_in_nanoseconds(self) -> None:
        with override_next_settings(DEFER={}):
            assert Thresholds.from_settings() == ADAPTIVE

    def test_set_values_are_read(self) -> None:
        placement = {
            "MODE": "pool",
            "HANDOFF": "1ms",
            "CPU_RATIO": 1,
            "CPU_MAX": 0.004,
            "EXPLORE": 0,
        }
        executor = {"OPTIONS": {"PLACEMENT": placement}}
        with override_next_settings(DEFER={"EXECUTOR": executor}):
            assert Thresholds.from_settings() == Thresholds(
                "pool", 1_000_000, 1.0, 4_000_000, 0
            )

    def test_a_misfit_reads_its_default(self) -> None:
        placement = {"MODE": "fast", "HANDOFF": -1, "EXPLORE": True}
        executor = {"OPTIONS": {"PLACEMENT": placement}}
        with override_next_settings(DEFER={"EXECUTOR": executor}):
            assert Thresholds.from_settings() == ADAPTIVE

    def test_an_executor_without_options_reads_the_defaults(self) -> None:
        executor = {"BACKEND": "next.suspense.LaneExecutor"}
        with override_next_settings(DEFER={"EXECUTOR": executor}):
            assert Thresholds.from_settings() == ADAPTIVE


class TestGilEnabled:
    """Tests for ``gil_enabled``."""

    def test_a_build_without_the_probe_holds_the_gil(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(sys, "_is_gil_enabled", raising=False)
        assert gil_enabled()

    @pytest.mark.parametrize("held", [True, False])
    def test_the_probe_decides(
        self, monkeypatch: pytest.MonkeyPatch, *, held: bool
    ) -> None:
        monkeypatch.setattr(sys, "_is_gil_enabled", lambda: held, raising=False)
        assert gil_enabled() is held


class TestPlace:
    """Tests for ``place``."""

    @pytest.mark.parametrize(
        ("facts", "parallel", "rule"),
        [
            (ForegroundFacts(Server.ASGI, atomic=("default",)), True, Rule.ATOMIC),
            (ForegroundFacts(Server.ASGI, sqlite=("default",)), None, Rule.SQLITE),
            (FACTS, False, Rule.OPT_OUT),
        ],
    )
    def test_a_rule_wins_over_everything(
        self, facts: ForegroundFacts, *, parallel: bool | None, rule: Rule
    ) -> None:
        placement = place(
            _job(parallel=parallel), facts, ADAPTIVE, CostTable(), alone=True
        )
        assert placement == Placement(rule)

    @pytest.mark.parametrize(
        ("parallel", "mode", "alone", "verdict"),
        [
            (True, "adaptive", True, Verdict.PINNED),
            (None, "pool", True, Verdict.POOL_MODE),
            (None, "adaptive", True, Verdict.ALONE),
        ],
    )
    def test_settings_and_a_lone_job_win_over_the_cost(
        self, mode: str, *, parallel: bool | None, alone: bool, verdict: Verdict
    ) -> None:
        options = Thresholds("pool" if mode == "pool" else "adaptive", 0, 0.8, 0, 0)
        table = _table(100, 100, 5)
        placement = place(_job(parallel=parallel), FACTS, options, table, alone=alone)
        assert placement == Placement(verdict)

    @pytest.mark.parametrize(
        ("wall", "cpu", "runs", "gil", "verdict"),
        [
            (100, 0, 0, True, Verdict.EXPLORE),
            (100, 0, 2, True, Verdict.EXPLORE),
            (100_000, 0, 3, True, Verdict.HANDOFF),
            (1_000_000, 900_000, 3, True, Verdict.CPU),
            (1_000_000, 900_000, 3, False, Verdict.COST),
            (3_000_000, 2_900_000, 3, True, Verdict.COST),
            (1_000_000, 100_000, 3, True, Verdict.COST),
        ],
    )
    def test_the_cost_model_decides_the_rest(
        self,
        monkeypatch: pytest.MonkeyPatch,
        wall: int,
        cpu: int,
        runs: int,
        *,
        gil: bool,
        verdict: Verdict,
    ) -> None:
        monkeypatch.setattr(sys, "_is_gil_enabled", lambda: gil, raising=False)
        table = _table(wall, cpu, runs)
        placement = place(_job(), FACTS, ADAPTIVE, table, alone=False)
        assert placement == Placement(verdict)

    @pytest.mark.parametrize(
        ("wall", "handoff", "verdict"),
        [
            (round(300_000 * (1 - BAND / 2)), False, Verdict.COST),
            (round(300_000 * (1 - BAND * 2)), False, Verdict.HANDOFF),
            (round(300_000 * (1 + BAND / 2)), True, Verdict.HANDOFF),
            (round(300_000 * (1 + BAND * 2)), True, Verdict.COST),
        ],
    )
    def test_a_site_at_the_handoff_bound_keeps_its_lane(
        self, wall: int, *, handoff: bool, verdict: Verdict
    ) -> None:
        table = _table(wall, 0, 3)
        table.lean(KEY, handoff=handoff)
        placement = place(_job(), FACTS, ADAPTIVE, table, alone=False)
        assert placement == Placement(verdict)
        assert table.cost(KEY).handoff is (verdict is Verdict.HANDOFF)

    def test_an_exploring_site_records_no_lane(self) -> None:
        table = _table(100, 0, 1)
        place(_job(), FACTS, ADAPTIVE, table, alone=False)
        assert table.cost(KEY) == Cost(100.0, 0.0, 1)
