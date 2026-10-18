from pathlib import Path

import pytest

from next.boundaries.baseline import (
    WHOLE,
    Debt,
    Purview,
    baseline_path,
    compare,
    dump,
    judged,
    read,
    shrink,
    tally,
    write,
)
from next.boundaries.errors import BaselineError
from next.boundaries.rules import CYCLE, GROWN, SCAN, STALE, Finding


_W = "next.W136"
_OTHER = "next.W138"


def _finding(file: str, line: int, code: str = _W, app: str = "shop") -> Finding:
    return Finding(file, line, code, app, f"{code} at {line}")


def _owner(file: str) -> str:
    return f"owner of {file}"


def _on_disk(root: Path, *files: str) -> None:
    for file in files:
        path = root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")


def _installed(file: str) -> str:
    return "" if file.startswith("crm/") else "shop"


class TestRead:
    """The ratchet holds exactly a version and its `[[violation]]` tables."""

    def test_no_file_is_no_record(self, tmp_path) -> None:
        assert read(tmp_path / "boundaries.toml") is None

    def test_a_valid_file(self, tmp_path) -> None:
        path = tmp_path / "boundaries.toml"
        path.write_text(
            "version = 1\n\n"
            '[[violation]]\nfile = "shop/views.py"\ncode = "next.W136"\ncount = 2\n\n'
            '[[violation]]\nfile = "shop/views.py"\ncode = "next.E167"\ncount = 1\n'
        )

        assert read(path) == {
            ("shop/views.py", "next.W136"): 2,
            ("shop/views.py", CYCLE): 1,
        }

    def test_a_version_alone_records_nothing(self, tmp_path) -> None:
        path = tmp_path / "boundaries.toml"
        path.write_text("version = 1\n")

        assert read(path) == {}

    @pytest.mark.parametrize(
        ("text", "reason"),
        [
            ("version = [", "It is no valid TOML."),
            ("version = 1\nextra = 2\n", "It has the unknown keys extra."),
            ("version = 2\n", "It must declare version = 1."),
            ("", "It must declare version = 1."),
            ("version = 1\nviolation = 3\n", "violation must be an array of tables."),
            (
                "version = 1\nviolation = [1]\n",
                "Entry 1 of [[violation]] must hold exactly the keys code, count, file.",
            ),
            (
                'version = 1\n[[violation]]\nfile = "a.py"\ncode = "next.W136"\n',
                "Entry 1 of [[violation]] must hold exactly the keys code, count, file.",
            ),
            (
                'version = 1\n[[violation]]\nfile = ""\ncode = "next.W136"\ncount = 1\n',
                "Entry 1 of [[violation]] names no file.",
            ),
            (
                'version = 1\n[[violation]]\nfile = 3\ncode = "next.W136"\ncount = 1\n',
                "Entry 1 of [[violation]] names no file.",
            ),
            (
                'version = 1\n[[violation]]\nfile = "a.py"\ncode = "next.E168"\ncount = 1\n',
                "Entry 1 of [[violation]] names 'next.E168', which is no boundary code.",
            ),
            (
                'version = 1\n[[violation]]\nfile = "a.py"\ncode = 3\ncount = 1\n',
                "Entry 1 of [[violation]] names 3, which is no boundary code.",
            ),
            (
                'version = 1\n[[violation]]\nfile = "a.py"\ncode = "next.W136"\ncount = 0\n',
                "Entry 1 of [[violation]] must count at least one finding.",
            ),
            (
                (
                    'version = 1\n[[violation]]\nfile = "a.py"\ncode = "next.W136"\n'
                    "count = true\n"
                ),
                "Entry 1 of [[violation]] must count at least one finding.",
            ),
            (
                (
                    'version = 1\n[[violation]]\nfile = "a.py"\ncode = "next.W136"\n'
                    'count = "2"\n'
                ),
                "Entry 1 of [[violation]] must count at least one finding.",
            ),
            (
                (
                    'version = 1\n[[violation]]\nfile = "a.py"\ncode = "next.W136"\n'
                    'count = 1\n[[violation]]\nfile = "a.py"\ncode = "next.W136"\n'
                    "count = 2\n"
                ),
                "It records next.W136 in a.py twice.",
            ),
        ],
    )
    def test_a_file_of_another_shape_is_refused(
        self, tmp_path, text: str, reason: str
    ) -> None:
        path = tmp_path / "boundaries.toml"
        path.write_text(text)

        with pytest.raises(BaselineError) as raised:
            read(path)

        assert raised.value.reason.startswith(reason)
        assert raised.value.path == path
        assert str(raised.value).startswith(f"{path} cannot be used. {reason}")

    def test_a_file_that_is_no_text(self, tmp_path) -> None:
        path = tmp_path / "boundaries.toml"
        path.write_bytes(b"version = '\xff'\n")

        with pytest.raises(BaselineError):
            read(path)

    def test_a_directory_in_its_place(self, tmp_path) -> None:
        path = tmp_path / "boundaries.toml"
        path.mkdir()

        with pytest.raises(BaselineError):
            read(path)


class TestWrite:
    """The written ratchet is sorted, quoted and reads back to the same counts."""

    def test_dump_sorts_by_file_then_code(self) -> None:
        text = dump({("b.py", _W): 1, ("a.py", _OTHER): 2, ("a.py", _W): 3})

        assert text == (
            "version = 1\n\n"
            '[[violation]]\nfile = "a.py"\ncode = "next.W136"\ncount = 3\n\n'
            '[[violation]]\nfile = "a.py"\ncode = "next.W138"\ncount = 2\n\n'
            '[[violation]]\nfile = "b.py"\ncode = "next.W136"\ncount = 1\n'
        )

    def test_nothing_to_record_is_the_version_alone(self) -> None:
        assert dump({}) == "version = 1\n"

    def test_an_odd_file_name_reads_back(self, tmp_path) -> None:
        counts = {('pages/[int:pk]/"quoted"\\päge.py', _W): 2}
        path = tmp_path / "boundaries.toml"

        write(path, counts)

        assert read(path) == counts

    def test_a_place_it_cannot_write(self, tmp_path) -> None:
        with pytest.raises(BaselineError):
            write(tmp_path / "missing" / "boundaries.toml", {})

    def test_the_path_under_the_base_dir(self) -> None:
        assert baseline_path(Path("/p")) == Path("/p/boundaries.toml")


class TestCompare:
    """A record covers exactly its count, and both more and fewer findings break it."""

    def test_no_record_shows_every_finding(self) -> None:
        found = [_finding("b.py", 2), _finding("a.py", 1), _finding("a.py", 0, SCAN)]

        ratchet = compare(found, None, _owner)

        assert ratchet.shown == tuple(sorted(found))
        assert ratchet.problems == ()
        assert ratchet.debts == (
            Debt("shop", _W, "a.py", 1, 0),
            Debt("shop", _W, "b.py", 1, 0),
        )

    def test_an_exact_record_covers_its_findings(self) -> None:
        found = [_finding("a.py", 1), _finding("a.py", 4)]

        ratchet = compare(found, {("a.py", _W): 2}, _owner)

        assert ratchet.shown == ()
        assert ratchet.problems == ()
        assert ratchet.debts == (Debt("shop", _W, "a.py", 2, 2),)

    def test_more_findings_than_recorded(self) -> None:
        found = [_finding("a.py", 4), _finding("a.py", 1), _finding("a.py", 9)]

        ratchet = compare(found, {("a.py", _W): 2}, _owner)

        assert ratchet.shown == ()
        assert ratchet.problems == (
            Finding(
                "a.py",
                0,
                GROWN,
                "shop",
                "Findings of next.W136 in the file: 3, recorded in boundaries.toml: "
                "2. Their lines: 4, 1, 9.",
            ),
        )

    def test_fewer_findings_than_recorded(self) -> None:
        ratchet = compare([_finding("a.py", 1)], {("a.py", _W): 3}, _owner)

        assert ratchet.problems == (
            Finding(
                "a.py",
                0,
                STALE,
                "shop",
                "Findings of next.W136 in a.py recorded in boundaries.toml: 3, "
                "remaining: 1.",
            ),
        )

    def test_a_record_with_no_finding_left(self) -> None:
        ratchet = compare([], {("gone.py", _W): 1}, _owner)

        assert ratchet.problems == (
            Finding(
                "gone.py",
                0,
                STALE,
                "owner of gone.py",
                "Findings of next.W136 in gone.py recorded in boundaries.toml: 1, "
                "remaining: 0.",
            ),
        )
        assert ratchet.debts == (Debt("owner of gone.py", _W, "gone.py", 0, 1),)

    def test_a_record_covers_its_code_only(self) -> None:
        found = [_finding("a.py", 1), _finding("a.py", 2, _OTHER)]

        ratchet = compare(found, {("a.py", _W): 1}, _owner)

        assert ratchet.shown == (_finding("a.py", 2, _OTHER),)


class TestShrink:
    """A refreeze records everything once, then only lowers what it recorded."""

    def test_no_file_records_every_finding(self) -> None:
        found = [_finding("a.py", 1), _finding("a.py", 2), _finding("a.py", 0, SCAN)]

        assert shrink(found, None) == ({("a.py", _W): 2}, 0)

    def test_a_file_only_shrinks(self) -> None:
        found = [
            _finding("a.py", 1),
            _finding("b.py", 1),
            _finding("b.py", 2),
            _finding("b.py", 3),
            _finding("new.py", 1),
        ]
        recorded = {("a.py", _W): 3, ("b.py", _W): 1, ("gone.py", _W): 2}

        assert shrink(found, recorded) == ({("a.py", _W): 1, ("b.py", _W): 1}, 3)

    def test_tally_counts_recordable_codes(self) -> None:
        found = [_finding("a.py", 1), _finding("a.py", 2), _finding("a.py", 0, GROWN)]

        assert tally(found) == {("a.py", _W): 2}


class TestScope:
    """Settings that leave apps out judge growth only, on files still on disk."""

    @pytest.mark.parametrize(
        ("files", "flags", "expected"),
        [
            (("crm/views.py",), {"dropping": False}, (("crm/views.py",), True)),
            ((), {"dropping": True}, ((), True)),
            ((), {"dropping": False}, ((), False)),
        ],
        ids=["a_record_of_a_dropped_app", "depends_on_drops", "whole"],
    )
    def test_judged(
        self,
        tmp_path,
        files: tuple[str, ...],
        flags: dict[str, bool],
        expected: tuple[tuple[str, ...], bool],
    ) -> None:
        _on_disk(tmp_path, "shop/views.py", *files)
        recorded = {("shop/views.py", _W): 1, ("crm/gone.py", _W): 1}
        recorded.update({(file, _W): 1 for file in files})

        scope = judged(tmp_path, recorded, _installed, **flags)

        assert (tuple(sorted(scope.away)), scope.reduced) == expected

    @pytest.mark.parametrize(
        ("base_dir", "recorded"),
        [(None, {("a.py", _W): 1}), (Path("/p"), None), (Path("/p"), {})],
        ids=["no_base_dir", "no_file", "no_record"],
    )
    def test_nothing_to_judge_is_the_whole(self, base_dir, recorded) -> None:
        assert judged(base_dir, recorded, _owner, dropping=True) is WHOLE

    @pytest.mark.parametrize(
        ("file", "expected"),
        [
            ("shop/views.py", (True, False)),
            ("crm/views.py", (True, True)),
            ("gone/views.py", (False, False)),
        ],
        ids=["remaining", "dropped_app", "deleted"],
    )
    def test_a_reduced_deploy(
        self, tmp_path, file: str, expected: tuple[bool, bool]
    ) -> None:
        _on_disk(tmp_path, "shop/views.py", "crm/views.py")
        scope = Purview(tmp_path, frozenset({"crm/views.py"}), reduced=True)

        assert (scope.may_shrink(file), scope.set_aside(file)) == expected

    def test_the_whole_project_is_sure_of_everything(self, tmp_path) -> None:
        _on_disk(tmp_path, "a.py")

        assert (WHOLE.may_shrink("a.py"), WHOLE.set_aside("a.py")) == (False, False)
        assert Purview(tmp_path).may_shrink("a.py") is False

    def test_compare_sets_the_records_of_a_dropped_app_aside(self, tmp_path) -> None:
        _on_disk(tmp_path, "shop/views.py", "crm/views.py")
        recorded = {("shop/views.py", _W): 2, ("crm/views.py", _W): 1}
        scope = judged(tmp_path, recorded, _installed, dropping=False)

        ratchet = compare([_finding("shop/views.py", 1)], recorded, _owner, scope)

        assert ratchet.problems == ()
        assert ratchet.shown == ()
        assert ratchet.debts == (Debt("shop", _W, "shop/views.py", 1, 2),)

    def test_compare_judges_growth_and_deleted_files(self, tmp_path) -> None:
        _on_disk(tmp_path, "shop/views.py")
        found = [_finding("shop/views.py", 1), _finding("shop/views.py", 2)]
        recorded = {("shop/views.py", _W): 1, ("crm/gone.py", _W): 1}
        scope = judged(tmp_path, recorded, _installed, dropping=True)

        ratchet = compare(found, recorded, _owner, scope)

        assert [(item.file, item.code) for item in ratchet.problems] == [
            ("crm/gone.py", STALE),
            ("shop/views.py", GROWN),
        ]

    def test_a_finding_in_a_file_set_aside_is_judged(self, tmp_path) -> None:
        _on_disk(tmp_path, "crm/views.py")
        scope = Purview(tmp_path, frozenset({"crm/views.py"}), reduced=True)

        ratchet = compare([_finding("crm/views.py", 1)], {}, _owner, scope)

        assert ratchet.shown == (_finding("crm/views.py", 1),)

    def test_shrink_keeps_what_it_cannot_judge(self, tmp_path) -> None:
        _on_disk(tmp_path, "shop/views.py", "crm/views.py")
        recorded = {
            ("shop/views.py", _W): 2,
            ("crm/views.py", _W): 1,
            ("crm/gone.py", _W): 1,
        }
        scope = judged(tmp_path, recorded, _installed, dropping=False)

        assert shrink([], recorded, scope) == (
            {("shop/views.py", _W): 2, ("crm/views.py", _W): 1},
            0,
        )
