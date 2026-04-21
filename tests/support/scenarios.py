from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch


if TYPE_CHECKING:
    from collections.abc import Generator

    from next.server import NextStatReloader


@contextmanager
def route_watch_layer_patches(
    *,
    get_pages_directories_for_watch,
    scan_pages_tree,
) -> Generator[None, None, None]:
    """Apply the usual ``next.server`` patches around route discovery for ``tick()`` tests."""
    with (
        patch(
            "next.server.autoreload.get_pages_directories_for_watch",
            get_pages_directories_for_watch,
        ),
        patch("next.server.autoreload.scan_pages_tree", scan_pages_tree),
    ):
        yield


@contextmanager
def tick_scenario_route_set_grows(reloader: NextStatReloader):
    """Watch dirs appear on the second call. Scan then returns a page when routes are ready."""
    fake_path = Path("/fake/pages/home/page.py")
    call_count = [0]

    def watch_side_effect():
        call_count[0] += 1
        return [] if call_count[0] == 1 else [Path("/fake/pages")]

    def scan_side_effect(pages_path):
        if call_count[0] < 2:
            return iter([])
        return iter([("home", pages_path / "home" / "page.py")])

    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=watch_side_effect,
            scan_pages_tree=scan_side_effect,
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify, fake_path.resolve()


@contextmanager
def tick_scenario_no_notify_first_tick(reloader: NextStatReloader):
    """Watch and scan stay stable. The first tick must not notify."""
    fake_dir = Path("/fake")
    fake_page = fake_dir / "page.py"
    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=lambda: [fake_dir],
            scan_pages_tree=lambda _p: iter([("home", fake_page)]),
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify


@contextmanager
def tick_scenario_route_set_unchanged(reloader: NextStatReloader):
    """Keep the same routes on every tick so notify stays silent."""
    fake_dir = Path("/fake")
    fake_page = fake_dir / "page.py"

    def route_iter(_path):
        return iter([("home", fake_page)])

    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=lambda: [fake_dir],
            scan_pages_tree=route_iter,
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify


@contextmanager
def tick_scenario_watch_raises(reloader: NextStatReloader):
    """If ``get_pages_directories_for_watch`` raises, the tick still runs."""
    with (
        patch(
            "next.server.autoreload.get_pages_directories_for_watch",
            side_effect=ValueError("bad"),
        ),
        patch.object(reloader, "snapshot_files", return_value=iter([])),
    ):
        yield


@contextmanager
def tick_scenario_mtime_change(reloader: NextStatReloader):
    """Snapshot mtime increases between ticks."""
    fake_path = Path("/fake/file.py")
    first_snapshot = [(fake_path, 1000.0)]
    second_snapshot = [(fake_path, 2000.0)]
    call_count = [0]

    def snapshot_side_effect():
        call_count[0] += 1
        return iter(first_snapshot if call_count[0] == 1 else second_snapshot)

    with (
        route_watch_layer_patches(
            get_pages_directories_for_watch=list,
            scan_pages_tree=lambda _p: iter([]),
        ),
        patch.object(reloader, "snapshot_files", side_effect=snapshot_side_effect),
        patch.object(reloader, "notify_file_changed") as mock_notify,
    ):
        yield mock_notify, fake_path


TICK_SCENARIOS: dict[str, object] = {
    "route_set_grows": tick_scenario_route_set_grows,
    "no_notify_first_tick": tick_scenario_no_notify_first_tick,
    "route_set_unchanged": tick_scenario_route_set_unchanged,
    "watch_raises": tick_scenario_watch_raises,
    "mtime_change": tick_scenario_mtime_change,
}


@contextmanager
def tick_scenario(name: str, reloader: NextStatReloader):
    """Dispatch named ``tick()`` patch scenario (for tests and indirect fixtures)."""
    fn = TICK_SCENARIOS[name]
    with fn(reloader) as stack:
        yield stack
