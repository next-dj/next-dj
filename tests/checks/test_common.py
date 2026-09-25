from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from django.core.checks import run_checks
from django.core.checks.registry import registry
from django.test import override_settings

from next.checks import _LAZY_SOURCES_BY_MODULE, NEXT, register_all, reset_check_caches
from next.checks.common import (
    RegistrationSubject,
    RunMemo,
    forget_run_memos,
    registration_file_errors,
)
from next.deps import resolver
from next.deps.introspect import introspect_key
from next.deps.resolver import forget_dep_caches
from next.pages.loaders import _page_roots, forget_page_roots
from next.pages.watch import _page_backends_for_watch, _state, forget_watch_state
from next.static.manager import forget_manager_page_roots, get_static_manager
from next.urls import PageRoot, RouterBackend, checks as urls_checks
from next.urls.checks import check_reverse_name_collisions, check_url_patterns
from tests.support import patch_checks_router_manager_with_routers, write_page


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


def _labelled_root(index: int, tree: Path) -> PageRoot:
    return PageRoot(path=tree, label="Root" if index == 0 else f"Root ({tree})")


class _RootTreeRouter(RouterBackend):
    """Third-party backend that reports root page trees and nothing else."""

    def __init__(self, root_trees: list[Path]) -> None:
        self._root_trees = list(root_trees)

    def generate_urls(self) -> list:
        return []

    def page_roots(self) -> list[PageRoot]:
        return [
            _labelled_root(index, tree) for index, tree in enumerate(self._root_trees)
        ]


class TestCollectAllPatternsDedup:
    """The two URL checks share one collection walk within a run."""

    def test_two_url_checks_collect_once_with_stable_messages(
        self, tmp_path: Path
    ) -> None:
        tree_a = tmp_path / "a"
        tree_b = tmp_path / "b"
        write_page(tree_a, "blog")
        write_page(tree_b, "blog")
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


class TestRunMemo:
    """A run memo builds once per key and forgets on a check-cache reset."""

    def test_a_repeat_with_the_same_key_builds_once(self) -> None:
        memo: RunMemo[list[int]] = RunMemo()
        key = object()
        first = memo.get(key, lambda: [1])
        assert memo.get(key, lambda: [2]) is first

    def test_a_new_key_builds_again(self) -> None:
        memo: RunMemo[list[int]] = RunMemo()
        memo.get(object(), lambda: [1])
        assert memo.get(object(), lambda: [2]) == [2]

    def test_an_equal_key_is_not_the_same_key(self) -> None:
        memo: RunMemo[str] = RunMemo()
        memo.get([1], lambda: "first")
        assert memo.get([1], lambda: "second") == "second"

    @pytest.mark.parametrize("forget", [forget_run_memos, reset_check_caches])
    def test_a_reset_drops_every_memo(self, forget) -> None:
        memo: RunMemo[str] = RunMemo()
        key = object()
        memo.get(key, lambda: "before")
        forget()
        assert memo.get(key, lambda: "after") == "after"


class TestRegisterAll:
    """`register_all` keeps the registered check set stable without server checks."""

    def test_it_imports_exactly_the_modules_the_map_names(self) -> None:
        with patch("next.checks.importlib.import_module") as spy:
            register_all()

        imported = [call.args[0] for call in spy.call_args_list]
        assert imported == list(_LAZY_SOURCES_BY_MODULE)

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


class TestRegistrationFileErrors:
    """`registration_file_errors` turns registry state into check messages."""

    subject = RegistrationSubject(
        decorator="@context",
        anchor_name="page.py",
        render="page render",
        code="next.E074",
    )

    def test_anchor_file_registration_reports_nothing(self, tmp_path: Path) -> None:
        errors = registration_file_errors(
            self.subject,
            registrations={tmp_path / "page.py": ("greeting",)},
            misattributed=[],
        )
        assert errors == []

    def test_helper_file_registration_reports_the_dead_binding(
        self, tmp_path: Path
    ) -> None:
        helper = tmp_path / "helpers.py"
        errors = registration_file_errors(
            self.subject,
            registrations={helper: ("greeting", "farewell")},
            misattributed=[],
        )
        assert [e.id for e in errors] == ["next.E074"]
        assert "farewell, greeting" in errors[0].msg

    def test_misattributed_name_is_not_repeated_by_the_helper_arm(
        self, tmp_path: Path
    ) -> None:
        helper = tmp_path / "helpers.py"
        page_file = tmp_path / "page.py"
        errors = registration_file_errors(
            self.subject,
            registrations={helper: ("greeting", "farewell")},
            misattributed=[(page_file, helper, "greeting")],
        )
        assert [e.id for e in errors] == ["next.E074", "next.E074"]
        assert "greeting" in errors[0].msg
        assert errors[1].msg.count("farewell") == 1
        assert "greeting" not in errors[1].msg

    def test_fully_misattributed_helper_reports_once(self, tmp_path: Path) -> None:
        helper = tmp_path / "helpers.py"
        page_file = tmp_path / "page.py"
        errors = registration_file_errors(
            self.subject,
            registrations={helper: ("greeting",)},
            misattributed=[(page_file, helper, "greeting")],
        )
        assert len(errors) == 1
        assert str(page_file) in errors[0].msg

    def test_records_are_ordered_by_their_paths(self, tmp_path: Path) -> None:
        helper = tmp_path / "helpers.py"
        first = tmp_path / "a" / "page.py"
        second = tmp_path / "b" / "page.py"
        errors = registration_file_errors(
            self.subject,
            registrations={},
            misattributed=[(second, helper, "later"), (first, helper, "earlier")],
        )
        assert [str(first) in errors[0].msg, str(second) in errors[1].msg] == [
            True,
            True,
        ]


@pytest.fixture()
def live_caches() -> Iterator[None]:
    """Empty the process-wide memos a check run must leave alone."""
    _forget_live_caches()
    yield
    _forget_live_caches()


def _forget_live_caches() -> None:
    forget_dep_caches()
    forget_page_roots()
    forget_watch_state()
    forget_manager_page_roots()


def _injected_view(request) -> None:
    """Plain callable the resolver compiles a plan for."""


class TestACheckRunLeavesTheLiveCachesWarm:
    """The managers a check run builds are its own, so no live memo is evicted."""

    def test_the_compiled_plan_cache_survives(self, live_caches) -> None:
        """The DI plans stay compiled, so the first request after a check is warm."""
        register_all()
        resolver.resolve_dependencies(_injected_view)
        key = introspect_key(_injected_view)
        entry = resolver._plan_cache[key]

        run_checks(tags=[NEXT])

        assert resolver._plan_cache[key] is entry

    def test_the_page_root_memos_survive(self, live_caches) -> None:
        """Both page-root memos stay filled, so no walk asks the routers again."""
        register_all()
        roots = _page_roots()
        static_roots = get_static_manager().page_roots()
        # An empty tuple is interned, so identity would pass a dropped memo too.
        assert roots
        assert static_roots

        run_checks(tags=[NEXT])

        assert _page_roots() is roots
        assert get_static_manager()._cached_page_roots is static_roots

    def test_the_watch_state_survives(self, live_caches) -> None:
        """The routers the watcher holds outlive a check run."""
        register_all()
        with override_settings(DEBUG=False):
            _page_backends_for_watch()
            memo = _state.memo
            assert memo is not None

            run_checks(tags=[NEXT])

            assert _state.memo is memo
