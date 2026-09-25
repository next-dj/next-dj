import functools
from pathlib import Path

import pytest

from next.pages.metadata import PageMetadataEntry, PageMetadataRegistry
from next.pages.metadata.chain import ChainEntry
from next.pages.metadata.schema import EMPTY_METADATA, Segment
from next.testing import SignalRecorder
from tests.support import handler_declared_here


def _wallet_meta() -> dict[str, str]:
    return {"title": "Wallet"}


def _other_meta() -> dict[str, str]:
    return {"title": "Other"}


def _entry(version: int = 0) -> ChainEntry:
    return ChainEntry(
        version=version,
        generation=0,
        site=Segment("site"),
        sources=(),
        folded=EMPTY_METADATA,
        static=EMPTY_METADATA,
    )


@pytest.fixture()
def registry() -> PageMetadataRegistry:
    return PageMetadataRegistry()


@pytest.fixture()
def page_file(tmp_path: Path) -> Path:
    return tmp_path / "page.py"


class TestRegistration:
    """One callable per file, the last registration winning."""

    def test_starts_empty_at_version_zero(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        assert registry.version == 0
        assert registry.entry(page_file) is None
        assert registry.registered_names() == {}
        assert registry.conflicts() == {}
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

    def test_the_last_registration_wins(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _other_meta, inherit=True)
        assert registry.entry(page_file) == PageMetadataEntry(_other_meta, True)
        assert registry.version == 2

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


class TestConflicts:
    """A second name on one file is kept for the diagnostic, a re-execution is not."""

    def test_two_names_on_one_file_are_recorded_in_order(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _other_meta)
        assert registry.conflicts() == {page_file: ("_wallet_meta", "_other_meta")}

    def test_the_same_name_again_is_no_conflict(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _wallet_meta)
        assert registry.conflicts() == {}

    def test_a_third_name_extends_the_record(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _other_meta)
        registry.register(page_file, handler_declared_here)
        assert registry.conflicts()[page_file] == (
            "_wallet_meta",
            "_other_meta",
            "handler_declared_here",
        )


class TestMisattribution:
    """A callable declared outside the decorating file lands in the log."""

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
    """A reset drops every record and moves the version on."""

    def test_reset_clears_entries_conflicts_and_misattributions(
        self, registry: PageMetadataRegistry, page_file: Path, tmp_path: Path
    ) -> None:
        registry.register(page_file, _wallet_meta)
        registry.register(page_file, _other_meta)
        registry.note_misattribution(page_file, tmp_path / "x.py", _wallet_meta)
        before = registry.version

        registry.reset()

        assert registry.entry(page_file) is None
        assert registry.conflicts() == {}
        assert registry.misattributed() == ()
        assert registry.version == before + 1

    def test_reset_leaves_the_chain_memo_to_forget_chains(
        self, registry: PageMetadataRegistry, page_file: Path
    ) -> None:
        registry._chains[page_file] = _entry()
        registry.reset()
        assert page_file in registry._chains
        registry.forget_chains()
        assert page_file not in registry._chains


class TestSignal:
    """`metadata_registered` names the file and whether the callable inherits."""

    def test_register_sends_file_path_and_inherit(
        self,
        registry: PageMetadataRegistry,
        page_file: Path,
        capture_metadata_registered: SignalRecorder,
    ) -> None:
        registry.register(page_file, _wallet_meta, inherit=True)
        assert len(capture_metadata_registered) == 1
        event = capture_metadata_registered.events[0]
        assert event.sender is PageMetadataRegistry
        assert event.kwargs["file_path"] == page_file
        assert event.kwargs["inherit"] is True
