import functools
import types
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from next.errors import AsyncCallableError
from next.pages.errors import PageMetadataConflictError
from next.pages.loaders import AncestorStamps
from next.pages.metadata.chain import ChainEntry
from next.pages.metadata.fold import EMPTY_STATE
from next.pages.metadata.markers import Metadata, Segment
from next.pages.metadata.registry import (
    MetadataRegistrations,
    PageMetadataEntry,
    PageMetadataRegistry,
)


def _wallet_meta() -> dict[str, str]:
    return {"title": "Wallet"}


def _other_meta() -> dict[str, str]:
    return {"title": "Other"}


def _rerun(func: Callable[[], dict[str, str]]) -> Callable[[], dict[str, str]]:
    """Return `func` as a re-executed `page.py` defines it, in a fresh namespace."""
    return types.FunctionType(func.__code__, {}, func.__name__)


def _entry() -> ChainEntry:
    return ChainEntry(
        ancestors=AncestorStamps(paths=(), version=0, watched=False),
        registry_stamps=(),
        registry_version=0,
        site=Segment("site"),
        sources=(),
        prefix=EMPTY_STATE,
        tail=(),
        static=Metadata(),
        folded=Metadata(),
    )


@pytest.fixture()
def page_file(tmp_path: Path) -> Path:
    return tmp_path / "page.py"


class TestRegistration:
    """One callable per file, a re-executed module replacing the one before."""

    def test_starts_empty_at_version_zero(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        assert registry.version == 0
        assert registry.entry(page_file) is None
        assert registry.registered_names() == {}
        assert registry.misattributed() == ()

    def test_register_stores_the_entry_and_bumps(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        assert registry.entry(page_file) == PageMetadataEntry(_wallet_meta, False)
        assert registry.version == 1

    def test_inherit_is_kept_on_the_entry(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta, inherit=True)
        entry = registry.entry(page_file)
        assert entry is not None
        assert entry.inherit is True

    def test_a_re_executed_module_replaces_the_callable(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        rerun = _rerun(_other_meta)
        registry.register(page_file, rerun, inherit=True)
        assert registry.entry(page_file) == PageMetadataEntry(rerun, True)
        assert registry.version == 2

    def test_a_second_callable_of_one_run_is_refused(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        with pytest.raises(PageMetadataConflictError) as caught:
            registry.register(page_file, _other_meta)
        assert "'_wallet_meta' and '_other_meta'" in str(caught.value)
        assert caught.value.file_path == page_file
        assert registry.entry(page_file) == PageMetadataEntry(_wallet_meta, False)

    def test_one_callable_registered_twice_is_no_conflict(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _wallet_meta, inherit=True)
        assert registry.entry(page_file) == PageMetadataEntry(_wallet_meta, True)

    def test_registered_names_carry_one_name_per_file(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        first = tmp_path / "a" / "page.py"
        second = tmp_path / "b" / "page.py"
        registry.register(first, _wallet_meta)
        registry.register(second, functools.partial(_other_meta))
        assert registry.registered_names() == {
            first: ("_wallet_meta",),
            second: ("_other_meta",),
        }


class TestStamps:
    """A stamp changes only when a registration changes the callable a chain runs."""

    def test_an_unregistered_path_has_no_stamp(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        assert registry.stamps((page_file,)) == (None,)

    def test_a_new_registration_stamps_the_path(
        self, registry: PageMetadataRegistry, page_file: Path, tmp_path: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        other = tmp_path / "other.py"
        (stamp, missing) = registry.stamps((page_file, other))
        assert stamp is not None
        assert missing is None

    def test_the_entry_is_written_before_the_stamp(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        """A build that reads the new stamp then reads the new entry as well."""
        seen: list[PageMetadataEntry | None] = []
        stamp = registry._stamp

        def recording(file_path: Path) -> None:
            seen.append(registry.entry(file_path))
            stamp(file_path)

        with patch.object(registry, "_stamp", side_effect=recording):
            registry.register(page_file, _wallet_meta)
        assert seen == [PageMetadataEntry(func=_wallet_meta, inherit=False)]

    def test_the_same_callable_again_keeps_the_stamp(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        before = registry.stamps((page_file,))
        registry.register(page_file, _wallet_meta)
        assert registry.stamps((page_file,)) == before

    @pytest.mark.parametrize(
        ("func", "inherit"),
        [(_rerun(_other_meta), False), (_wallet_meta, True)],
        ids=["other_name", "other_inherit"],
    )
    def test_a_changed_registration_moves_the_stamp(
        self,
        registry: PageMetadataRegistry,
        page_file: Path,
        func: Callable[[], dict[str, str]],
        *,
        inherit: bool,
    ) -> None:
        registry.register(page_file, _wallet_meta)
        before = registry.stamps((page_file,))
        registry.register(page_file, func, inherit=inherit)
        assert registry.stamps((page_file,)) != before

    def test_a_reset_drops_every_stamp(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.reset()
        assert registry.stamps((page_file,)) == (None,)


class TestRegistrations:
    """The diagnostics read the names and the misattributions in one call."""

    def test_the_two_views_travel_together(
        self, registry: PageMetadataRegistry, page_file: Path, tmp_path: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _rerun(_other_meta))
        registry.note_misattribution(page_file, tmp_path / "x.py", _wallet_meta)
        assert registry.registrations() == MetadataRegistrations(
            names={page_file: ("_other_meta",)}, misattributed=registry.misattributed()
        )


class TestMisattribution:
    """A callable declared outside the decorating file is recorded in the log."""

    def test_note_records_the_pair_once(
        self, registry: PageMetadataRegistry, tmp_path: Path
    ) -> None:
        registered_from = tmp_path / "page.py"
        declared_in = tmp_path / "helpers.py"
        registry.note_misattribution(registered_from, declared_in, _wallet_meta)
        registry.note_misattribution(registered_from, declared_in, _wallet_meta)
        records = registry.misattributed()
        assert [(r.registered_from, r.declared_in, r.name) for r in records] == [
            (registered_from, declared_in, "_wallet_meta")
        ]


class TestReset:
    """A reset drops every record and memoised chain and increments the version."""

    def test_reset_clears_entries_and_misattributions(
        self, registry: PageMetadataRegistry, page_file: Path, tmp_path: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _rerun(_other_meta))
        registry.note_misattribution(page_file, tmp_path / "x.py", _wallet_meta)
        before = registry.version

        registry.reset()

        assert registry.entry(page_file) is None
        assert registry.misattributed() == ()
        assert registry.version == before + 1

    def test_every_write_reports_to_the_change_callback(self, page_file: Path) -> None:
        writes: list[int] = []
        registry = PageMetadataRegistry(on_change=lambda: writes.append(1))

        registry.register(page_file, _wallet_meta)
        registry.reset()

        assert writes == [1, 1]

    def test_reset_drops_the_chain_memo_too(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.remember(page_file, _entry())
        registry.reset()
        assert registry.chain(page_file) is None


async def _async_meta() -> dict[str, str]:
    return {"title": "Async"}


async def _streamed_meta():
    yield {"title": "Streamed"}


class TestMetadataColour:
    """A coroutine metadata callable makes the chain async, an async generator raises."""

    def test_a_coroutine_function_registers_with_its_colour(self, registry) -> None:
        registry.register(Path("/m/page.py"), _async_meta)

        assert registry.entry(Path("/m/page.py")).is_async is True
        assert registry.awaits(Path("/m/page.py")) is True

    def test_an_async_generator_is_refused(self, registry) -> None:
        with pytest.raises(AsyncCallableError, match=r"@page\.metadata callable"):
            registry.register(Path("/m/page.py"), _streamed_meta)

    def test_a_sync_chain_does_not_await(self, registry) -> None:
        registry.register(Path("/m/page.py"), _wallet_meta, inherit=True)

        assert registry.awaits(Path("/m/leaf/page.py")) is False

    def test_an_inherited_ancestor_callable_makes_the_page_await(
        self, registry
    ) -> None:
        registry.register(Path("/m/page.py"), _async_meta, inherit=True)

        assert registry.awaits(Path("/m/leaf/page.py")) is True

    def test_an_ancestor_callable_kept_to_its_own_page_does_not(self, registry) -> None:
        registry.register(Path("/m/page.py"), _async_meta)

        assert registry.awaits(Path("/m/leaf/page.py")) is False
