from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.core.checks import run_checks
from django.core.checks.registry import registry
from django.test import override_settings

from next.checks import NEXT, register_all
from next.checks.common import (
    get_components_manager,
    get_router_manager,
    iter_scanned_page_pairs,
    reset_components_manager_cache,
    reset_router_manager_cache,
)
from next.conf.signals import settings_reloaded
from next.urls import checks as urls_checks
from next.urls.checks import check_reverse_name_collisions, check_url_patterns
from next.urls.dispatcher import scan_pages_tree
from tests.support import patch_checks_router_manager_with_routers


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clean_manager_caches() -> Iterator[None]:
    reset_router_manager_cache()
    reset_components_manager_cache()
    urls_checks.reset_collected_patterns_cache()
    yield
    reset_router_manager_cache()
    reset_components_manager_cache()
    urls_checks.reset_collected_patterns_cache()


def _write_page(tree: Path, route: str) -> Path:
    directory = tree / route
    directory.mkdir(parents=True, exist_ok=True)
    page_file = directory / "page.py"
    page_file.write_text('template = "ok"\n')
    return page_file


class _ScanSpyRouter:
    """Root router that walks a real tree and counts each scan call."""

    app_dirs = False

    def __init__(self, tree: Path) -> None:
        self.pages_dir = str(tree)
        self._tree = tree
        self.scan_calls = 0

    def _get_root_pages_paths(self) -> list[Path]:
        return [self._tree]

    def _scan_pages_directory(self, pages_dir: Path) -> Iterator[tuple[str, Path]]:
        self.scan_calls += 1
        yield from scan_pages_tree(pages_dir)


class _RootTreeRouter:
    """Root router exposing several page trees for URL pattern collection."""

    app_dirs = False
    _skip_dir_names: frozenset[str] = frozenset()

    def __init__(self, root_trees: list[Path]) -> None:
        self._root_trees = list(root_trees)

    def _get_root_pages_paths(self) -> list[Path]:
        return list(self._root_trees)


class TestRouterManagerCache:
    """`get_router_manager` reuses one manager per check run."""

    def test_built_once_across_repeated_calls(self) -> None:
        with patch("next.urls.RouterManager") as mock_cls:
            first = get_router_manager()
            second = get_router_manager()
            third = get_router_manager()
        assert first is second is third
        assert mock_cls.call_count == 1
        assert mock_cls.return_value.reload.call_count == 1

    def test_explicit_reset_forces_rebuild(self) -> None:
        with patch("next.urls.RouterManager") as mock_cls:
            get_router_manager()
            reset_router_manager_cache()
            get_router_manager()
        assert mock_cls.call_count == 2
        assert mock_cls.return_value.reload.call_count == 2

    def test_settings_reloaded_signal_resets_cache(self) -> None:
        with patch("next.urls.RouterManager") as mock_cls:
            get_router_manager()
            settings_reloaded.send(sender=None)
            get_router_manager()
        assert mock_cls.call_count == 2

    def test_init_error_result_is_cached(self) -> None:
        with patch("next.urls.RouterManager", side_effect=ImportError("boom")):
            manager, errors = get_router_manager()
            second_manager, second_errors = get_router_manager()
        assert manager is None
        assert second_manager is None
        assert errors is second_errors
        assert errors[0].id == "next.E007"


class TestComponentsManagerCache:
    """`get_components_manager` reuses one manager per check run."""

    def test_built_once_across_repeated_calls(self) -> None:
        with patch("next.components.manager.ComponentsManager") as mock_cls:
            first = get_components_manager()
            second = get_components_manager()
            third = get_components_manager()
        assert first is second is third
        assert mock_cls.call_count == 1
        assert mock_cls.return_value._reload_config.call_count == 1

    def test_explicit_reset_forces_rebuild(self) -> None:
        with patch("next.components.manager.ComponentsManager") as mock_cls:
            get_components_manager()
            reset_components_manager_cache()
            get_components_manager()
        assert mock_cls.call_count == 2
        assert mock_cls.return_value._reload_config.call_count == 2

    def test_settings_reloaded_signal_resets_cache(self) -> None:
        with patch("next.components.manager.ComponentsManager") as mock_cls:
            get_components_manager()
            settings_reloaded.send(sender=None)
            get_components_manager()
        assert mock_cls.call_count == 2


class TestScannedPairsCache:
    """`iter_scanned_page_pairs` materialises one scan per router per run."""

    def test_two_consumptions_scan_once(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _ScanSpyRouter(tmp_path)

        first = list(iter_scanned_page_pairs(router))
        second = list(iter_scanned_page_pairs(router))

        assert first == second
        assert router.scan_calls == 1

    def test_cached_pairs_match_direct_scan(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        _write_page(tmp_path, "docs/guide")
        router = _ScanSpyRouter(tmp_path)

        cached = list(iter_scanned_page_pairs(router))
        direct = list(scan_pages_tree(tmp_path))

        assert cached == direct

    def test_distinct_routers_cache_independently(self, tmp_path: Path) -> None:
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        _write_page(tree_a, "one")
        _write_page(tree_b, "two")
        router_a = _ScanSpyRouter(tree_a)
        router_b = _ScanSpyRouter(tree_b)

        pairs_a = list(iter_scanned_page_pairs(router_a))
        list(iter_scanned_page_pairs(router_a))
        pairs_b = list(iter_scanned_page_pairs(router_b))
        list(iter_scanned_page_pairs(router_b))

        assert router_a.scan_calls == 1
        assert router_b.scan_calls == 1
        assert pairs_a != pairs_b

    def test_explicit_reset_rescans(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _ScanSpyRouter(tmp_path)

        list(iter_scanned_page_pairs(router))
        reset_router_manager_cache()
        list(iter_scanned_page_pairs(router))

        assert router.scan_calls == 2

    def test_settings_reloaded_signal_rescans(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _ScanSpyRouter(tmp_path)

        list(iter_scanned_page_pairs(router))
        settings_reloaded.send(sender=None)
        list(iter_scanned_page_pairs(router))

        assert router.scan_calls == 2

    def test_new_pages_visible_only_after_reset(self, tmp_path: Path) -> None:
        _write_page(tmp_path, "blog")
        router = _ScanSpyRouter(tmp_path)

        before = list(iter_scanned_page_pairs(router))
        _write_page(tmp_path, "about")
        frozen = list(iter_scanned_page_pairs(router))
        reset_router_manager_cache()
        after = list(iter_scanned_page_pairs(router))

        assert frozen == before
        assert len(after) == len(before) + 1


class TestCollectAllPatternsDedup:
    """The two URL checks share one collection walk within a run."""

    def test_two_url_checks_collect_once_with_stable_messages(
        self, tmp_path: Path
    ) -> None:
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        _write_page(tree_a, "blog")
        _write_page(tree_b, "blog")
        router = _RootTreeRouter(root_trees=[tree_a, tree_b])

        with (
            patch_checks_router_manager_with_routers(routers=[router]),
            patch(
                "next.urls.checks._collect_all_patterns_uncached",
                wraps=urls_checks._collect_all_patterns_uncached,
            ) as spy,
        ):
            memo_url = check_url_patterns(None)
            memo_rev = check_reverse_name_collisions(None)

        assert spy.call_count == 1
        assert any(m.id == "next.E015" for m in memo_url)

        urls_checks.reset_collected_patterns_cache()
        with patch_checks_router_manager_with_routers(routers=[router]):
            control_url = check_url_patterns(None)
            urls_checks.reset_collected_patterns_cache()
            control_rev = check_reverse_name_collisions(None)

        assert [(m.id, m.msg) for m in memo_url] == [(m.id, m.msg) for m in control_url]
        assert [(m.id, m.msg) for m in memo_rev] == [(m.id, m.msg) for m in control_rev]


class TestRegisterAll:
    """`register_all` keeps the registered check set stable without server checks."""

    def test_register_all_registers_same_check_set(self) -> None:
        before = {
            getattr(check, "__name__", None) for check in registry.registered_checks
        }
        register_all()
        after = {
            getattr(check, "__name__", None) for check in registry.registered_checks
        }
        assert after == before
        assert len(after) == len(before)


class TestNextTag:
    """The `next` tag selects only `next-dj` checks for `manage.py check`."""

    def test_next_tag_runs_next_checks(self) -> None:
        register_all()
        with override_settings(NEXT_FRAMEWORK={"__unknown_top_level__": True}):
            messages = run_checks(tags=[NEXT])
        assert messages
        assert any(message.id == "next.E035" for message in messages)
        assert all(message.id.startswith("next.") for message in messages)

    def test_unregistered_tag_runs_nothing(self) -> None:
        register_all()
        assert run_checks(tags=["__not_a_real_tag__"]) == []

    def test_no_next_check_carries_compatibility_tag(self) -> None:
        register_all()
        next_checks = [
            check
            for check in registry.registered_checks
            if getattr(check, "__module__", "").startswith("next.")
        ]
        assert next_checks
        assert all("compatibility" not in check.tags for check in next_checks)
