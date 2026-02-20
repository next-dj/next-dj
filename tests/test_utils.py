from pathlib import Path
from unittest.mock import patch

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

    def test_tick_notify_when_route_set_grows(self) -> None:
        """When route set (from watch dirs + scan) grows, notify_file_changed is called."""
        reloader = NextStatReloader()
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
            patch(
                "next.utils.get_pages_directories_for_watch",
                side_effect=watch_side_effect,
            ),
            patch("next.utils._scan_pages_directory", side_effect=scan_side_effect),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)  # first tick: previous_route_set = {}
            next(gen)  # second tick: current != previous -> notify
            mock_notify.assert_called_once()
            mock_notify.assert_called_with(fake_path.resolve())

    def test_tick_no_notify_on_first_tick(self) -> None:
        """First tick only stores route set, does not notify."""
        reloader = NextStatReloader()
        fake_dir = Path("/fake")
        fake_page = fake_dir / "page.py"
        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                return_value=[fake_dir],
            ),
            patch(
                "next.utils._scan_pages_directory",
                return_value=iter([("home", fake_page)]),
            ),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)
            mock_notify.assert_not_called()

    def test_tick_no_notify_when_route_set_unchanged(self) -> None:
        """When route/layout/template sets are unchanged, notify_file_changed is not called."""
        reloader = NextStatReloader()
        fake_dir = Path("/fake")
        fake_page = fake_dir / "page.py"

        def route_iter(_path):
            return iter([("home", fake_page)])

        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                return_value=[fake_dir],
            ),
            patch(
                "next.utils._scan_pages_directory",
                side_effect=route_iter,
            ),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)
            next(gen)
            mock_notify.assert_not_called()

    def test_tick_swallows_exception_from_route_set_build(self) -> None:
        """If building route set raises (e.g. get_pages_directories_for_watch), tick continues."""
        reloader = NextStatReloader()
        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                side_effect=ValueError("bad"),
            ),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
        ):
            gen = reloader.tick()
            next(gen)

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

    def test_tick_notify_when_template_set_changes(self) -> None:
        """When template.djx set changes, notify_file_changed is called with that path."""
        reloader = NextStatReloader()
        fake_dir = Path("/fake")
        fake_page = fake_dir / "page.py"
        fake_template = Path("/fake/pages/foo/template.djx").resolve()
        call_count = [0]

        def templates_side_effect():
            call_count[0] += 1
            return set() if call_count[0] == 1 else {fake_template}

        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                return_value=[fake_dir],
            ),
            patch(
                "next.utils._scan_pages_directory",
                return_value=iter([("home", fake_page)]),
            ),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch(
                "next.utils.get_template_djx_paths_for_watch",
                side_effect=templates_side_effect,
            ),
            patch.object(reloader, "snapshot_files", return_value=iter([])),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)
            next(gen)
            calls = [c[0][0] for c in mock_notify.call_args_list]
            assert fake_template in calls

    def test_tick_notify_when_file_mtime_changes(self) -> None:
        """When snapshot_files returns a file whose mtime increases, notify_file_changed is called."""
        reloader = NextStatReloader()
        fake_path = Path("/fake/file.py")
        first_snapshot = [(fake_path, 1000.0)]
        second_snapshot = [(fake_path, 2000.0)]
        call_count = [0]

        def snapshot_side_effect():
            call_count[0] += 1
            return iter(first_snapshot if call_count[0] == 1 else second_snapshot)

        with (
            patch(
                "next.utils.get_pages_directories_for_watch",
                return_value=[],
            ),
            patch("next.utils.get_layout_djx_paths_for_watch", return_value=set()),
            patch("next.utils.get_template_djx_paths_for_watch", return_value=set()),
            patch.object(reloader, "snapshot_files", side_effect=snapshot_side_effect),
            patch.object(reloader, "notify_file_changed") as mock_notify,
        ):
            gen = reloader.tick()
            next(gen)  # first tick: stores mtime 1000
            next(gen)  # second tick: mtime 2000 > 1000 -> notify
            mock_notify.assert_called_once_with(fake_path)
