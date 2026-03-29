from pathlib import Path
from unittest.mock import patch

import pytest
from django.utils.autoreload import StatReloader

from next.utils import NextStatReloader


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
            pytest.param(
                "template_set_changes",
                2,
                "template_notify",
                id="template_set_changes",
            ),
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
        elif expect == "template_notify":
            mock_notify, fake_template = payload
            calls = [c[0][0] for c in mock_notify.call_args_list]
            assert fake_template in calls

    def test_check_layouts_notify_when_layout_used_by_route(self) -> None:
        """_check_layouts notifies when layout in diff is under a route's dir."""
        reloader = NextStatReloader()
        reloader._previous_layouts = set()
        layout_djx = Path("/app/pages/blog/layout.djx").resolve()
        page_py = Path("/app/pages/blog/page.py").resolve()
        routes = {("blog", page_py)}
        with patch.object(reloader, "notify_file_changed") as mock_notify:
            reloader._check_layouts({layout_djx}, routes)
        mock_notify.assert_called_once_with(layout_djx)

    def test_check_layouts_no_notify_when_layout_not_used_by_route(self) -> None:
        """_check_layouts does not notify when no route is under the layout dir."""
        reloader = NextStatReloader()
        reloader._previous_layouts = set()
        unused_layout = Path("/app/pages/other/layout.djx").resolve()
        page_py = Path("/app/pages/blog/page.py").resolve()
        routes = {("blog", page_py)}
        with patch.object(reloader, "notify_file_changed") as mock_notify:
            reloader._check_layouts({unused_layout}, routes)
        mock_notify.assert_not_called()
        assert reloader._previous_layouts == {unused_layout}

    def test_check_layouts_skips_value_error_from_is_relative_to(self) -> None:
        """_check_layouts continues when is_relative_to raises ValueError."""
        reloader = NextStatReloader()
        reloader._previous_layouts = set()
        layout_djx = Path("/app/pages/blog/layout.djx").resolve()
        route_path = Path("/app/pages/blog/page.py").resolve()
        routes = {("blog", route_path)}

        with (
            patch.object(reloader, "notify_file_changed") as mock_notify,
            patch.object(
                Path,
                "is_relative_to",
                side_effect=ValueError("not relative"),
            ),
        ):
            reloader._check_layouts({layout_djx}, routes)
        mock_notify.assert_not_called()
        assert reloader._previous_layouts == {layout_djx}

    def test_check_templates_notify_when_set_changes(self) -> None:
        """_check_templates notifies when template set changes."""
        reloader = NextStatReloader()
        reloader._previous_templates = set()
        template_djx = Path("/app/pages/foo/template.djx").resolve()
        with patch.object(reloader, "notify_file_changed") as mock_notify:
            reloader._check_templates({template_djx})
        mock_notify.assert_called_once_with(template_djx)
