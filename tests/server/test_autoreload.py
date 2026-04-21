from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils.autoreload import StatReloader

from next.server import NextStatReloader


def _paths_matched_by_reloader_globs(reloader: StatReloader) -> set[Path]:
    """Paths returned by ``Path.glob`` for each registered ``watch_dir`` pair."""
    out: set[Path] = set()
    for directory, patterns in reloader.directory_globs.items():
        for pattern in patterns:
            out.update(p.resolve() for p in directory.glob(pattern))
    return out


class TestNextStatReloader:
    """Tests for NextStatReloader."""

    def test_subclasses_stat_reloader(self) -> None:
        """NextStatReloader is a subclass of Django StatReloader."""
        assert issubclass(NextStatReloader, StatReloader)

    def test_tick_is_generator(self) -> None:
        """tick() yields and can be iterated."""
        reloader = NextStatReloader()
        with patch.object(reloader, "snapshot_files", return_value=iter([])):
            gen = reloader.tick()
            first = next(gen)
            assert first is None

    def test_tick_calls_snapshot_files(self) -> None:
        """tick() uses snapshot_files for mtime check."""
        reloader = NextStatReloader()
        with patch.object(
            reloader, "snapshot_files", return_value=iter([])
        ) as mock_snapshot:
            gen = reloader.tick()
            next(gen)
            mock_snapshot.assert_called()

    @pytest.mark.parametrize(
        ("reloader_tick_scenario", "num_ticks", "expect"),
        [
            pytest.param("route_set_grows", 2, "notify_path", id="route_set_grows"),
            pytest.param(
                "no_notify_first_tick", 1, "no_notify", id="no_notify_first_tick"
            ),
            pytest.param(
                "route_set_unchanged", 2, "no_notify", id="route_set_unchanged"
            ),
            pytest.param("watch_raises", 1, "ok", id="watch_raises"),
            pytest.param("mtime_change", 2, "notify_path", id="mtime_change"),
        ],
        indirect=["reloader_tick_scenario"],
    )
    def test_tick_scenario_notify_behavior(
        self,
        reloader_tick_scenario,
        num_ticks: int,
        expect: str,
    ) -> None:
        """tick() under each patched scenario matches expected notify behavior."""
        reloader, payload = reloader_tick_scenario
        gen = reloader.tick()
        for _ in range(num_ticks):
            next(gen)
        if expect == "notify_path":
            mock_notify, expected_path = payload
            mock_notify.assert_called_once_with(expected_path)
        elif expect == "no_notify":
            mock_notify = payload
            mock_notify.assert_not_called()
        elif expect == "ok":
            assert payload is None


class TestDjxNotInStatReloaderGlobMatches:
    """``.djx`` files are not among paths matched by next's ``watch_dir`` globs."""

    def test_glob_matched_paths_exclude_djx_alongside_page_and_components(
        self, tmp_path: Path
    ) -> None:
        """Mimic production globs under a pages root. No ``.djx`` is glob-matched."""
        pages = tmp_path / "pages"
        home = pages / "home"
        home.mkdir(parents=True)
        (home / "page.py").write_text("#")
        (home / "template.djx").write_text("<p/>")
        chip = pages / "_components" / "chip"
        chip.mkdir(parents=True)
        (chip / "component.djx").write_text("<span/>")
        (chip / "component.py").write_text("#")
        (pages / "_components" / "solo.djx").write_text("<div/>")

        reloader = NextStatReloader()
        reloader.watch_dir(pages, "**/page.py")
        reloader.watch_dir(pages, "**/_components/**/component.py")

        matched = _paths_matched_by_reloader_globs(reloader)
        assert (home / "template.djx").resolve() not in matched
        assert (chip / "component.djx").resolve() not in matched
        assert (pages / "_components" / "solo.djx").resolve() not in matched
        assert (home / "page.py").resolve() in matched
        assert (chip / "component.py").resolve() in matched

    def test_changing_only_djx_does_not_call_notify_file_changed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Editing ``.djx`` does not go through ``notify_file_changed`` (no process reload)."""
        page = tmp_path / "p" / "page.py"
        page.parent.mkdir(parents=True)
        page.write_text("#")
        djx = tmp_path / "p" / "template.djx"
        djx.write_text("a")

        reloader = NextStatReloader()
        reloader.watch_dir(tmp_path, "**/page.py")
        notified: list[Path] = []

        def record_notify(p: Path) -> None:
            notified.append(Path(p))

        monkeypatch.setattr(reloader, "notify_file_changed", record_notify)

        with (
            patch(
                "next.server.autoreload.get_pages_directories_for_watch",
                return_value=[],
            ),
            patch("next.server.autoreload.scan_pages_tree", return_value=iter([])),
            patch.object(
                reloader,
                "snapshot_files",
                return_value=iter([(page.resolve(), 1000.0)]),
            ),
        ):
            gen = reloader.tick()
            next(gen)
            djx.write_text("changed")
            next(gen)

        assert notified == []
