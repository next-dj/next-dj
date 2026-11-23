from pathlib import Path

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group
from django.db import DatabaseError, transaction

from next.deps.runner import run_on_request_thread
from next.pages import page
from next.pages.boundaries import Caught
from next.pages.graph import Blame
from next.pages.loaders import NO_BOUNDARIES, BoundaryChain, PageBoundary
from next.transactions import LazySavepoint, probe_atomic_aliases
from tests.support.statements import query_missing_table


class Stop(BaseException):
    """A non-`Exception` failure no boundary contains."""


def _stop() -> str:
    raise Stop


_GUARDED = BoundaryChain.of((PageBoundary(0, 0, Path("/site/error.djx"), None),))


async def _amissing_table() -> None:
    await run_on_request_thread(query_missing_table)


def _boom_value() -> None:
    msg = "body"
    raise ValueError(msg)


async def _aboom_value() -> None:
    _boom_value()


def _contain(mode: str, page_file: Path, work: str) -> object:
    if mode == "sync":
        failing = query_missing_table if work == "read" else _boom_value
        return page._contained(_GUARDED, page_file, Blame(), failing)
    afailing = _amissing_table() if work == "read" else _aboom_value()
    return async_to_sync(page._acontained)(_GUARDED, page_file, Blame(), afailing)


class TestBaseException:
    """A non-`Exception` failure undoes the work and propagates past every boundary."""

    def test_a_base_exception_undoes_the_work_and_propagates(self, tmp_path) -> None:
        with pytest.raises(Stop):
            page._contained(NO_BOUNDARIES, tmp_path / "page.py", Blame(), _stop)

    def test_an_awaited_base_exception_undoes_the_work_and_propagates(
        self, tmp_path
    ) -> None:
        async def stop() -> str:
            return _stop()

        contained = async_to_sync(page._acontained)
        with pytest.raises(Stop):
            contained(NO_BOUNDARIES, tmp_path / "page.py", Blame(), stop())


@pytest.mark.django_db()
@pytest.mark.parametrize("mode", ["sync", "async"])
class TestContainedRollback:
    """A page boundary contains a failure only once its rollback held."""

    def test_a_failed_read_without_a_savepoint_is_left_to_the_one_above(
        self, tmp_path, mode: str
    ) -> None:
        with transaction.atomic():
            with LazySavepoint(probe_atomic_aliases()) as outer:
                list(Group.objects.all())
                with pytest.raises(DatabaseError, match="next_missing_table"):
                    _contain(mode, tmp_path / "page.py", "read")
                assert outer.rollback()
            assert not transaction.get_rollback()

    def test_a_failed_read_under_its_own_savepoint_is_contained(
        self, tmp_path, mode: str
    ) -> None:
        with transaction.atomic():
            caught = _contain(mode, tmp_path / "page.py", "read")
            assert isinstance(caught, Caught)
            assert isinstance(caught.error, DatabaseError)
            assert not transaction.get_rollback()

    def test_a_python_error_under_an_outer_savepoint_is_contained(
        self, tmp_path, mode: str
    ) -> None:
        with transaction.atomic(), LazySavepoint(probe_atomic_aliases()):
            list(Group.objects.all())
            caught = _contain(mode, tmp_path / "page.py", "boom")
        assert isinstance(caught, Caught)
        assert isinstance(caught.error, ValueError)
