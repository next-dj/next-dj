from collections.abc import Iterator

import pytest
from django.contrib.auth.models import User
from django.core.paginator import Paginator

from next.seo.pagination import ChainedEntries, LanguagePairs, Part, count_rows


class SpyRows:
    """A sequence recording every slice and every full iteration it answers."""

    def __init__(self, size: int) -> None:
        """Hold `size` rows and no record yet."""
        self.rows = list(range(size))
        self.slices: list[tuple[int, int]] = []
        self.iterated = 0

    def __len__(self) -> int:
        """Return the rows held."""
        return len(self.rows)

    def __getitem__(self, key: slice) -> list[int]:
        """Record the slice and answer its rows."""
        self.slices.append((key.start, key.stop))
        return self.rows[key]

    def __iter__(self) -> Iterator[int]:
        """Record a full read and answer every row."""
        self.iterated += 1
        return iter(self.rows)


def _part(rows, *, lastmod_field: str | None = None) -> Part[int]:
    return Part(rows, lambda row: row * 10, lastmod_field=lastmod_field)


class TestCountRows:
    """A count follows the paginator rule, a no-argument `count()` ahead of `len`."""

    def test_a_list_and_a_range_count_by_length(self) -> None:
        assert count_rows([1, 2, 3]) == 3
        assert count_rows(range(5)) == 5

    def test_a_no_argument_count_method_answers(self) -> None:
        class Counted(SpyRows):
            def count(self) -> int:
                return 99

        assert count_rows(Counted(2)) == 99

    @pytest.mark.django_db()
    def test_a_queryset_counts_in_one_query(self, django_assert_num_queries) -> None:
        User.objects.create(username="a")
        with django_assert_num_queries(1):
            assert count_rows(User.objects.all()) == 1


class TestPart:
    """A part converts only the rows a slice asks for and counts them once."""

    def test_a_slice_reads_only_its_rows(self) -> None:
        rows = SpyRows(10)
        part = _part(rows)
        assert part.slice(2, 4) == [20, 30]
        assert rows.slices == [(2, 4)]
        assert rows.iterated == 0

    def test_the_count_is_read_once(self) -> None:
        class Counted(SpyRows):
            calls = 0

            def count(self) -> int:
                Counted.calls += 1
                return len(self.rows)

        part = _part(Counted(3))
        assert (part.count(), part.count()) == (3, 3)
        assert Counted.calls == 1


class TestChainedEntries:
    """The parts read as one sequence, a page slicing each part it crosses once."""

    def test_page_three_of_ten_reads_one_slice_without_iterating(self) -> None:
        rows = SpyRows(100)
        entries = ChainedEntries([_part(rows)])
        page = Paginator(entries, 10).page(3)
        assert list(page.object_list) == [row * 10 for row in range(20, 30)]
        assert rows.slices == [(20, 30)]
        assert rows.iterated == 0

    def test_a_slice_across_a_seam_reads_the_tail_and_the_head(self) -> None:
        first, second = SpyRows(5), SpyRows(5)
        entries = ChainedEntries([_part(first), _part(second)])
        assert entries[3:7] == [30, 40, 0, 10]
        assert first.slices == [(3, 5)]
        assert second.slices == [(0, 2)]

    def test_a_slice_inside_the_second_part_skips_the_first(self) -> None:
        first, second = SpyRows(5), SpyRows(5)
        entries = ChainedEntries([_part(first), _part(second)])
        assert entries[6:8] == [10, 20]
        assert first.slices == []

    def test_an_empty_part_in_the_middle_is_passed_over(self) -> None:
        entries = ChainedEntries([_part([1]), _part([]), _part([2])])
        assert entries[0:2] == [10, 20]
        assert len(entries) == 2

    def test_indexing_reads_one_row(self) -> None:
        entries = ChainedEntries([_part([1, 2]), _part([3])])
        assert (entries[0], entries[2], entries[-1]) == (10, 30, 30)

    @pytest.mark.parametrize("index", [3, -4])
    def test_an_index_outside_raises(self, index: int) -> None:
        entries = ChainedEntries([_part([1, 2, 3])])
        with pytest.raises(IndexError, match="out of a sequence of 3"):
            entries[index]

    def test_an_empty_or_reversed_slice_reads_nothing(self) -> None:
        rows = SpyRows(3)
        entries = ChainedEntries([_part(rows)])
        assert entries[2:1] == []
        assert entries[5:9] == []
        assert rows.slices == []

    def test_a_stepped_slice_is_refused(self) -> None:
        with pytest.raises(ValueError, match="step of one"):
            ChainedEntries([_part([1, 2])])[::2]

    def test_iteration_reads_every_part(self) -> None:
        assert list(ChainedEntries([_part([1]), _part([2, 3])])) == [10, 20, 30]


class TestLanguagePairs:
    """Every entry pairs with every language, a page slicing the entries it needs."""

    def test_the_count_multiplies_by_the_languages(self) -> None:
        pairs = LanguagePairs(ChainedEntries([_part(SpyRows(4))]), ["en", "de"])
        assert len(pairs) == 8

    def test_a_page_reads_the_entries_under_it_once(self) -> None:
        rows = SpyRows(10)
        pairs = LanguagePairs(ChainedEntries([_part(rows)]), ["en", "de", "fr"])
        assert pairs[4:8] == [(10, "de"), (10, "fr"), (20, "en"), (20, "de")]
        assert rows.slices == [(1, 3)]

    def test_an_index_reads_one_pair(self) -> None:
        pairs = LanguagePairs(ChainedEntries([_part([1, 2])]), ["en", "de"])
        assert pairs[3] == (20, "de")

    def test_no_language_pairs_nothing(self) -> None:
        pairs = LanguagePairs(ChainedEntries([_part([1, 2])]), [])
        assert len(pairs) == 0
        assert pairs[0:5] == []
        assert list(pairs) == []

    def test_a_paginated_page_of_pairs(self) -> None:
        pairs = LanguagePairs(ChainedEntries([_part(range(5))]), ["en", "de"])
        page = Paginator(pairs, 4).page(2)
        assert list(page.object_list) == [
            (20, "en"),
            (20, "de"),
            (30, "en"),
            (30, "de"),
        ]
