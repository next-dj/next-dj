from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.checks.registry import registry

from next.components.info import ComponentInfo
from next.pages.loaders import forget_layout_texts
from next.suspense.checks import check_durable_jobs, jobs as jobs_module
from next.suspense.connections import ConnectionState
from next.testing import override_next_settings
from tests.support import patch_checks_router_manager, write_page


_DURABLE_PAGE = (
    "from django.db import transaction\n"
    "from next.pages import context\n\n"
    "@context('rows')\n"
    "def rows():\n"
    "    with transaction.atomic(durable=True):\n"
    "        return 1\n\n"
    "template = 'ok'\n"
)
_STATE = {"CONNECTION_STATE": ["tests.suspense.checks.test_jobs.Replayed"]}


class Replayed(ConnectionState):
    """A connection state backend that replays nothing."""

    def capture(self, request: object, alias: str) -> None:
        return None

    def apply(self, state: object, connection: object) -> None:
        return None


def _component(root: Path, name: str, source: str | None) -> ComponentInfo:
    folder = root / name
    folder.mkdir()
    module_path = None
    if source is not None:
        module_path = folder / "component.py"
        module_path.write_text(source)
    return ComponentInfo(name, root, "", folder / "component.djx", module_path, False)


class TestDurableJobs:
    """next.W173 flags `durable=True` in code a deferred job runs with connection state."""

    def _messages(self, *infos: ComponentInfo) -> list[object]:
        with (
            patch.object(jobs_module, "deferred_components", return_value=infos),
            patch.object(jobs_module, "load_routed_pages", return_value=[]),
            override_next_settings(DEFER=_STATE),
        ):
            return check_durable_jobs()

    def test_a_component_passing_durable_is_flagged(self, tmp_path: Path) -> None:
        info = _component(tmp_path, "card", "atomic(durable=True)\n")
        [message] = self._messages(info)
        assert message.id == "next.W173"
        assert info.module_path is not None
        assert message.obj == str(info.module_path.resolve())
        assert "CONNECTION_STATE" in message.msg

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("atomic(durable=False)\n", id="not-durable"),
            pytest.param("atomic(durable=flag)\n", id="unknown-value"),
            pytest.param("def broken(:\n", id="syntax-error"),
            pytest.param(None, id="no-module"),
        ],
    )
    def test_anything_else_is_quiet(self, tmp_path: Path, source: str | None) -> None:
        assert self._messages(_component(tmp_path, "card", source)) == []

    def test_an_unreadable_module_is_skipped(self, tmp_path: Path) -> None:
        info = _component(tmp_path, "card", "atomic(durable=True)\n")
        assert info.module_path is not None
        info.module_path.unlink()
        assert self._messages(info) == []

    def test_no_connection_state_is_quiet(self, tmp_path: Path) -> None:
        info = _component(tmp_path, "card", "atomic(durable=True)\n")
        with patch.object(jobs_module, "deferred_components", return_value=[info]):
            assert check_durable_jobs() == []

    @pytest.fixture()
    def shop(self, tmp_path: Path) -> Path:
        """Write a page with a boundary and a plain one, both passing `durable=True`."""
        (tmp_path / "layout.djx").write_text("<html>{% template %}</html>")
        (tmp_path / "shop").mkdir()
        (tmp_path / "shop" / "loading.djx").write_text("<p>loading</p>")
        forget_layout_texts()
        page_file = write_page(tmp_path, "shop", _DURABLE_PAGE)
        write_page(tmp_path, "plain", _DURABLE_PAGE)
        return page_file

    def test_a_page_with_a_boundary_is_scanned(
        self, tmp_path: Path, shop: Path
    ) -> None:
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch.object(jobs_module, "deferred_components", return_value=[]),
            override_next_settings(DEFER=_STATE),
        ):
            messages = check_durable_jobs()
        assert [(m.id, m.obj) for m in messages] == [("next.W173", str(shop.resolve()))]

    def test_a_job_callable_without_code_adds_no_source(
        self, tmp_path: Path, shop: Path
    ) -> None:
        steps = (SimpleNamespace(func=len),)
        plan = SimpleNamespace(
            graph=SimpleNamespace(steps=steps),
            split=SimpleNamespace(jobs=[(None, {0})]),
        )
        with (
            patch_checks_router_manager(pages_directory=tmp_path),
            patch.object(jobs_module, "deferred_components", return_value=[]),
            patch.object(jobs_module.page, "gating_plan", return_value=plan),
            override_next_settings(DEFER=_STATE),
        ):
            messages = check_durable_jobs()
        assert [m.obj for m in messages] == [str(shop.resolve())]


def test_the_job_check_is_registered() -> None:
    assert check_durable_jobs in registry.get_checks()
