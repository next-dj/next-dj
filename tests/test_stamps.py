from concurrent.futures import ThreadPoolExecutor

import pytest

from next.stamps import (
    RACY_MTIME_WINDOW_NS,
    all_fresh,
    fresh,
    settled,
    snapshot,
    stamp_of,
    stat_mtime_ns,
    trusted,
)
from tests.support import TICK_NS, rewrite_in_tick


_IN_THE_WINDOW = pytest.mark.parametrize(
    "tick",
    [0, RACY_MTIME_WINDOW_NS - 1, -RACY_MTIME_WINDOW_NS + 1],
    ids=["same-tick", "just-before-settling", "slightly-in-the-future"],
    indirect=True,
)

_PAST_THE_WINDOW = pytest.mark.parametrize(
    "tick",
    [RACY_MTIME_WINDOW_NS, -RACY_MTIME_WINDOW_NS],
    ids=["aged", "far-in-the-future"],
    indirect=True,
)


class TestStatMtimeNs:
    """The raw nanosecond mtime of a path, `None` for one that does not stat."""

    def test_a_file_reports_the_nanoseconds_its_stat_carries(self, tmp_path) -> None:
        target = tmp_path / "f.txt"
        target.write_text("x")
        assert stat_mtime_ns(target) == target.stat().st_mtime_ns

    def test_a_path_that_does_not_stat_reports_nothing(self, tmp_path) -> None:
        assert stat_mtime_ns(tmp_path / "never-written") is None


class TestSettled:
    """An mtime is settled once it leaves the racy window around the clock."""

    @_IN_THE_WINDOW
    def test_an_mtime_inside_the_window_is_unsettled(self, tick) -> None:
        assert settled(TICK_NS) is False

    @_PAST_THE_WINDOW
    def test_an_mtime_outside_the_window_is_settled(self, tick) -> None:
        assert settled(TICK_NS) is True


class TestStampOf:
    """A settled mtime is its own stamp, an unsettled one a token no stat equals."""

    @_PAST_THE_WINDOW
    def test_a_settled_mtime_is_its_own_stamp(self, tick) -> None:
        assert stamp_of(TICK_NS) == TICK_NS

    @_IN_THE_WINDOW
    def test_an_unsettled_mtime_gets_a_new_token_each_time(self, tick) -> None:
        first, second = stamp_of(TICK_NS), stamp_of(TICK_NS)
        assert first < 0
        assert second < 0
        assert first != second

    @pytest.mark.usefixtures("tick")
    def test_tokens_drawn_from_many_threads_never_repeat(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            tokens = list(pool.map(lambda _: stamp_of(TICK_NS), range(400)))
        assert len(set(tokens)) == len(tokens)


class TestTrusted:
    """Only a token of an unsettled mtime fails to vouch for a read."""

    @pytest.mark.parametrize(
        ("stamp", "expected"),
        [(None, True), (0, True), (TICK_NS, True), (-1, False)],
        ids=["absent", "epoch", "settled", "token"],
    )
    def test_trust(self, stamp: int | None, *, expected: bool) -> None:
        assert trusted(stamp) is expected

    @pytest.mark.usefixtures("tick")
    def test_a_token_is_never_trusted(self) -> None:
        assert not trusted(stamp_of(TICK_NS))


class TestSnapshot:
    """A snapshot stamps the current mtime of a path, `None` when it does not stat."""

    def test_a_snapshot_of_a_path_that_does_not_stat_is_nothing(self, tmp_path) -> None:
        assert snapshot(tmp_path / "never-written") is None

    @_PAST_THE_WINDOW
    def test_a_snapshot_after_the_window_keeps_the_mtime(self, tmp_path, tick) -> None:
        rewrite_in_tick(tmp_path)
        assert snapshot(tmp_path) == TICK_NS

    @pytest.mark.usefixtures("tick")
    def test_a_snapshot_in_the_tick_of_the_path_is_untrusted(self, tmp_path) -> None:
        rewrite_in_tick(tmp_path)
        assert not trusted(snapshot(tmp_path))


class TestFresh:
    """A stamp is fresh while the path stats to it, which a token never does."""

    def test_an_unmoved_path_matches_its_snapshot(self, tmp_path) -> None:
        assert fresh(snapshot(tmp_path), tmp_path)

    def test_a_moved_mtime_is_stale(self, tmp_path) -> None:
        held = snapshot(tmp_path)
        rewrite_in_tick(tmp_path)
        assert not fresh(held, tmp_path)

    def test_a_path_absent_on_both_reads_is_fresh(self, tmp_path) -> None:
        assert fresh(None, tmp_path / "never-written")

    def test_a_deleted_path_is_stale(self, tmp_path) -> None:
        path = tmp_path / "f.txt"
        path.write_text("x")
        held = snapshot(path)
        path.unlink()
        assert not fresh(held, path)

    @pytest.mark.usefixtures("tick")
    def test_two_snapshots_in_one_tick_never_pass_as_equal(self, tmp_path) -> None:
        rewrite_in_tick(tmp_path)
        held = snapshot(tmp_path)
        assert held != snapshot(tmp_path)
        assert not fresh(held, tmp_path)

    def test_every_pair_must_match(self, tmp_path) -> None:
        first = tmp_path / "a"
        second = tmp_path / "b"
        first.write_text("a")
        second.write_text("b")
        held = [(first, snapshot(first)), (second, snapshot(second))]
        assert all_fresh(held)
        rewrite_in_tick(second)
        assert not all_fresh(held)

    def test_no_pairs_are_fresh(self) -> None:
        assert all_fresh([])
