from __future__ import annotations

import pytest
from django.contrib.auth.models import Group
from django.test import RequestFactory

from next.partial import KeysetPager, NumberPager
from next.partial.paging import RESTORE_PARAM


_PER_PAGE = 20
_ROWS = list(range(1000))
_SAVED = 200
_NUMBERS = NumberPager(per_page=_PER_PAGE)
_COUNTED = NumberPager(per_page=_PER_PAGE, count=True)
_KEYSET = KeysetPager(("name",), per_page=_PER_PAGE)
_factory = RequestFactory()


class TestBenchNumberPagerOverList:
    """The window of a `{% more %}` list cut out of rows already in memory."""

    @pytest.mark.benchmark(group="partial.more")
    def test_window_next_page(self, benchmark) -> None:
        """One page appended, read one row past its end and never counted."""
        request = _factory.get("/feed/", {"page": "3"})
        window = benchmark(_NUMBERS.window, _ROWS, request)
        assert list(window) == _ROWS[40:60]
        assert window.next == "4"

    @pytest.mark.benchmark(group="partial.more")
    def test_window_restored_run(self, benchmark) -> None:
        """Five pages at once, as a back navigation restores the list it left."""
        request = _factory.get("/feed/", {"page": "5", RESTORE_PARAM: "page"})
        window = benchmark(_NUMBERS.window, _ROWS, request)
        assert len(window) == 5 * _PER_PAGE
        assert window.backs == ("1", "2", "3", "4")

    @pytest.mark.benchmark(group="partial.more")
    def test_window_counted(self, benchmark) -> None:
        """The same single page with `count=True`, which asks the list its length."""
        request = _factory.get("/feed/", {"page": "3"})
        window = benchmark(_COUNTED.window, _ROWS, request)
        assert window.count == len(_ROWS)


@pytest.mark.django_db()
class TestBenchPagersOverQueryset:
    """Both pagers over a queryset, one bounded query for a batch."""

    @pytest.fixture()
    def groups(self) -> None:
        Group.objects.bulk_create(Group(name=f"g{i:04}") for i in range(_SAVED))

    @pytest.mark.usefixtures("groups")
    @pytest.mark.benchmark(group="partial.more")
    def test_number_next_page(self, benchmark) -> None:
        request = _factory.get("/feed/", {"page": "3"})
        rows = Group.objects.order_by("name")
        window = benchmark(_NUMBERS.window, rows, request)
        assert window.rows[0].name == "g0040"
        assert window.next == "4"

    @pytest.mark.usefixtures("groups")
    @pytest.mark.benchmark(group="partial.more")
    def test_number_counted(self, benchmark) -> None:
        """The counted page, which adds a `COUNT` to the slice."""
        request = _factory.get("/feed/", {"page": "3"})
        rows = Group.objects.order_by("name")
        window = benchmark(_COUNTED.window, rows, request)
        assert window.count == _SAVED

    @pytest.mark.usefixtures("groups")
    @pytest.mark.benchmark(group="partial.more")
    def test_keyset_next_batch(self, benchmark) -> None:
        """The batch after a cursor, its token decoded and the next two written."""
        token = _KEYSET.encode(("g0039",), reverse=False)
        request = _factory.get("/feed/", {"cursor": token})
        window = benchmark(_KEYSET.window, Group.objects.all(), request)
        assert window.rows[0].name == "g0040"
        assert window.next == _KEYSET.encode(("g0059",), reverse=False)

    @pytest.mark.usefixtures("groups")
    @pytest.mark.benchmark(group="partial.more")
    def test_keyset_restored_run(self, benchmark) -> None:
        """Five batches restored around a cursor, two bounded queries."""
        token = _KEYSET.encode(("g0079",), reverse=False)
        request = _factory.get("/feed/", {"cursor": token, RESTORE_PARAM: "cursor"})
        window = benchmark(_KEYSET.window, Group.objects.all(), request)
        assert len(window) == 5 * _PER_PAGE
        assert len(window.backs) == 4
