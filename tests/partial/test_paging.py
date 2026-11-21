from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Annotated
from uuid import UUID

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models import (
    DateTimeField,
    DecimalField,
    DurationField,
    IntegerField,
    Model,
    UUIDField,
)
from django.test import RequestFactory, override_settings
from django.test.utils import isolate_apps

from next.deps import ResolutionContext, resolver
from next.deps.cache import DependencyCache
from next.partial import (
    KeysetPager,
    NumberPager,
    Pager,
    Paging,
    Window,
    paging as paging_module,
)
from next.partial.paging import PagingProvider, pager_of, valid_param
from tests.support import inspect_parameter
from tests.support.cases.paging import (
    MALFORMED_TOKENS,
    NUMBER_CASES,
    NumberCase,
    TokenCase,
    raw_token,
)


factory = RequestFactory()
ROWS = list(range(95))
NAMES = [f"g{index:03}" for index in range(95)]
MOMENT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
WALK_LIMIT = 64


def _get(**query: object):
    return factory.get("/", query)


def _context(request) -> ResolutionContext:
    return ResolutionContext(
        request=request,
        form=None,
        url_kwargs={},
        context_data={},
        cache=DependencyCache({}),
    )


def _names(window: Window) -> list[str]:
    return [row.name for row in window]


def _forward(pager: Pager, rows, request=None) -> list[Window]:
    windows = [pager.window(rows, request)]
    while windows[-1].next is not None:
        assert len(windows) < WALK_LIMIT, "the walk repeats a window"
        windows.append(pager.window(rows, _get(**{pager.param: windows[-1].next})))
    return windows


def _backward(pager: Pager, rows, token: str) -> list[Window]:
    windows = [pager.window(rows, _get(**{pager.param: token}))]
    while windows[-1].previous is not None:
        assert len(windows) < WALK_LIMIT, "the walk repeats a window"
        windows.append(pager.window(rows, _get(**{pager.param: windows[-1].previous})))
    return windows


@pytest.fixture()
def groups() -> None:
    Group.objects.bulk_create(Group(name=name) for name in NAMES)


@pytest.fixture()
def users() -> list[User]:
    """Twenty-five users over three join dates, so the leading column ties."""
    return User.objects.bulk_create(
        User(username=f"u{index:02}", date_joined=MOMENT - timedelta(days=index % 3))
        for index in range(25)
    )


@pytest.fixture()
def ledger(transactional_db) -> Iterator[type[Model]]:
    """Install a model with datetime, decimal, uuid and nullable columns."""
    with isolate_apps("tests"):

        class Entry(Model):
            stamp = DateTimeField()
            amount = DecimalField(max_digits=8, decimal_places=2)
            ref = UUIDField(unique=True)
            span = DurationField()
            rank = IntegerField(null=True)

            class Meta:
                app_label = "tests"

            def __str__(self) -> str:
                return str(self.ref)

        with connection.schema_editor() as editor:
            editor.create_model(Entry)
        try:
            yield Entry
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(Entry)


class TestWindow:
    """A window iterates and measures its rows, so a template loops over it."""

    def test_it_iterates_its_rows(self) -> None:
        window = Window(rows=[1, 2, 3], param="page")
        assert list(window) == [1, 2, 3]
        assert len(window) == 3

    def test_an_empty_window_is_falsy(self) -> None:
        assert not Window(rows=[], param="page")


class TestPagerOptions:
    """Both pagers refuse a parameter or a batch size they could not serve."""

    @pytest.mark.parametrize(
        "param", ["p q", "restore", "", 5], ids=["space", "restore", "empty", "int"]
    )
    def test_a_bad_param_is_refused(self, param: object) -> None:
        with pytest.raises(ValueError, match="query parameter"):
            NumberPager(param=param)

    @pytest.mark.parametrize("per_page", [0, -1, True], ids=["zero", "neg", "bool"])
    def test_a_bad_batch_size_is_refused(self, per_page: int) -> None:
        with pytest.raises(ValueError, match="per_page"):
            KeysetPager(("pk",), per_page=per_page)

    def test_valid_param(self) -> None:
        assert valid_param("cursor") is True
        assert valid_param("restore") is False
        assert valid_param(None) is False


class TestNumberPager:
    """`?page=N` reads one page and a restore the pages up to it."""

    @pytest.mark.parametrize("case", NUMBER_CASES, ids=lambda c: c.id)
    def test_query(self, case: NumberCase) -> None:
        window = NumberPager(per_page=10).window(ROWS, factory.get(f"/?{case.query}"))
        assert list(window) == list(case.rows)
        assert (window.next, window.previous) == (case.next, case.previous)
        assert window.backs == case.backs
        assert window.back == (None if case.next is None else str(int(case.next) - 1))
        assert (window.param, window.count) == ("page", None)

    def test_no_request_is_page_one(self) -> None:
        window = NumberPager(per_page=10).window(ROWS, None)
        assert list(window) == list(range(10))

    def test_the_parameter_name_is_read(self) -> None:
        window = NumberPager(param="p", per_page=5).window(ROWS, _get(p=4, page=9))
        assert list(window) == list(range(15, 20))
        assert window.param == "p"

    def test_the_restore_reach_follows_the_setting(self) -> None:
        pager = NumberPager(per_page=10)
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"MAX_RESTORE": 3}}}):
            window = pager.window(ROWS, _get(page=6, restore=1))
        assert list(window) == list(range(30, 60))
        assert (window.previous, window.backs, window.back) == ("3", ("4", "5"), "6")

    def test_an_exact_multiple_ends_on_a_full_page(self) -> None:
        window = NumberPager(per_page=10).window(list(range(100)), _get(page=10))
        assert len(window) == 10
        assert window.next is None

    def test_an_empty_list_is_one_empty_window(self) -> None:
        window = NumberPager().window([], None)
        assert (list(window), window.next, window.previous) == ([], None, None)

    def test_count_is_read_only_when_asked(self) -> None:
        window = NumberPager(per_page=10, count=True).window(ROWS, None)
        assert window.count == 95

    @pytest.mark.django_db()
    def test_a_window_is_one_bounded_query(
        self, groups: None, django_assert_num_queries
    ) -> None:
        rows = Group.objects.order_by("name")
        with django_assert_num_queries(1) as captured:
            window = NumberPager(per_page=5).window(rows, _get(page=12, restore=1))
        sql = captured.captured_queries[0]["sql"].upper()
        assert "LIMIT 51 OFFSET 10" in sql
        assert "COUNT" not in sql
        assert _names(window) == NAMES[10:60]
        assert window.backs == tuple(str(page) for page in range(3, 12))

    @pytest.mark.django_db()
    def test_a_count_is_one_more_query(
        self, groups: None, django_assert_num_queries
    ) -> None:
        pager = NumberPager(per_page=10, count=True)
        with django_assert_num_queries(2):
            window = pager.window(Group.objects.order_by("name"), None)
        assert window.count == 95

    @pytest.mark.django_db()
    def test_the_async_window_reads_the_same_rows(
        self, groups: None, django_assert_num_queries
    ) -> None:
        pager = NumberPager(per_page=10, count=True)
        rows = Group.objects.order_by("name")
        with django_assert_num_queries(2):
            window = async_to_sync(pager.awindow)(rows, _get(page=3, restore=1))
        assert _names(window) == NAMES[:30]
        assert (window.next, window.back, window.backs) == ("4", "3", ("1", "2"))
        assert window.count == 95

    def test_the_async_window_slices_a_sequence(self) -> None:
        pager = NumberPager(per_page=10, count=True)
        window = async_to_sync(pager.awindow)(ROWS, _get(page=2))
        assert (list(window), window.count) == (list(range(10, 20)), 95)


@pytest.mark.django_db()
class TestKeysetTraversal:
    """A keyset pager walks every row once in both directions, ties included."""

    def test_forward_walks_every_row_once(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        windows = _forward(pager, Group.objects.all())
        assert [len(window) for window in windows] == [10] * 9 + [5]
        assert [name for window in windows for name in _names(window)] == NAMES
        assert windows[0].previous is None
        assert all(window.previous is not None for window in windows[1:])

    def test_backward_walks_every_row_once(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        last = _forward(pager, Group.objects.all())[-1]
        windows = _backward(pager, Group.objects.all(), last.previous)
        names = [name for window in reversed(windows) for name in _names(window)]
        assert names == NAMES[:90]
        assert windows[-1].previous is None

    def test_an_exact_multiple_ends_without_an_empty_fetch(self, groups: None) -> None:
        Group.objects.bulk_create(
            Group(name=f"g{index:03}") for index in range(95, 100)
        )
        windows = _forward(KeysetPager(("name",), per_page=10), Group.objects.all())
        assert [len(window) for window in windows] == [10] * 10
        assert windows[-1].next is None

    def test_ties_on_the_leading_column_resolve_by_the_tail(self, users) -> None:
        pager = KeysetPager(("-date_joined", "pk"), per_page=4)
        windows = _forward(pager, User.objects.all())
        walked = [user.pk for window in windows for user in window]
        expected = list(
            User.objects.order_by("-date_joined", "pk").values_list("pk", flat=True)
        )
        assert walked == expected
        assert len(set(walked)) == 25

    def test_mixed_directions_walk_both_ways(self, users) -> None:
        pager = KeysetPager(("-date_joined", "username"), per_page=4)
        rows = User.objects.all()
        expected = list(
            rows.order_by("-date_joined", "username").values_list("username", flat=True)
        )
        windows = _forward(pager, rows)
        assert [user.username for window in windows for user in window] == expected
        back = _backward(pager, rows, windows[-1].previous)
        walked = [user.username for window in reversed(back) for user in window]
        assert walked == expected[: len(walked)]
        assert len(walked) == 24

    def test_an_empty_source_is_one_empty_window(self) -> None:
        pager = KeysetPager(("name",), per_page=10)
        window = pager.window(Group.objects.all(), _get(cursor=raw_token([False, "a"])))
        assert (list(window), window.next, window.previous) == ([], None, None)

    def test_every_query_is_bounded(
        self, groups: None, django_assert_num_queries
    ) -> None:
        pager = KeysetPager(("-name", "pk"), per_page=10)
        first = pager.window(Group.objects.all(), None)
        with django_assert_num_queries(2) as captured:
            pager.window(Group.objects.all(), _get(cursor=first.next, restore=1))
        for query in captured.captured_queries:
            sql = query["sql"].upper()
            assert "LIMIT" in sql
            assert "COUNT" not in sql

    def test_count_is_read_only_when_asked(
        self, groups: None, django_assert_num_queries
    ) -> None:
        pager = KeysetPager(("name",), per_page=10, count=True)
        with django_assert_num_queries(2):
            window = pager.window(Group.objects.all(), None)
        assert window.count == 95

    def test_the_back_token_returns_the_batch_before_the_next(
        self, groups: None
    ) -> None:
        pager = KeysetPager(("name",), per_page=10)
        first = pager.window(Group.objects.all(), None)
        assert first.back is not None
        before = pager.window(Group.objects.all(), _get(cursor=first.back))
        assert _names(before) == NAMES[:10]
        assert before.previous is None
        assert before.next == first.next


@pytest.mark.django_db()
class TestKeysetRestore:
    """A restore ends on the batch its token fetches, under whole batches before it."""

    def _token_after(self, pager: KeysetPager, name: str) -> str:
        return pager.encode((name,), reverse=False)

    def test_a_restore_reaches_back_to_the_start(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = self._token_after(pager, "g039")
        window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(window) == NAMES[:50]
        assert window.previous is None
        assert window.next == self._token_after(pager, "g049")
        assert window.back == pager.encode(("g050",), reverse=True)
        assert window.backs == tuple(
            pager.encode((name,), reverse=True)
            for name in ("g010", "g020", "g030", "g040")
        )

    def test_each_back_token_fetches_the_batch_before_it(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = self._token_after(pager, "g039")
        window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        for index, back in enumerate(window.backs):
            batch = pager.window(Group.objects.all(), _get(cursor=back))
            assert _names(batch) == NAMES[index * 10 : index * 10 + 10]

    def test_a_partial_batch_on_top_is_left_to_previous(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=25)
        token = self._token_after(pager, "g059")
        window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(window) == NAMES[10:85]
        assert window.backs == (
            pager.encode(("g035",), reverse=True),
            pager.encode(("g060",), reverse=True),
        )
        top = pager.window(Group.objects.all(), _get(cursor=window.previous))
        assert _names(top) == NAMES[:10]
        assert top.previous is None

    def test_the_reach_follows_the_setting(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = self._token_after(pager, "g059")
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"MAX_RESTORE": 3}}}):
            window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(window) == NAMES[40:70]
        assert window.previous == pager.encode(("g040",), reverse=True)

    def test_a_restore_of_one_batch_reads_one_query(
        self, groups: None, django_assert_num_queries
    ) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = self._token_after(pager, "g059")
        with (
            override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"MAX_RESTORE": 1}}}),
            django_assert_num_queries(1),
        ):
            window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(window) == NAMES[60:70]
        assert window.backs == ()

    def test_a_restore_at_the_end_keeps_every_row(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = self._token_after(pager, "g089")
        window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(window) == NAMES
        assert (window.next, window.back, window.backs) == (None, None, ())
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"MAX_RESTORE": 3}}}):
            short = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(short) == NAMES[70:]
        assert short.previous == pager.encode(("g070",), reverse=True)

    def test_a_reverse_restore_holds_whole_batches(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = pager.encode(("g025",), reverse=True)
        window = pager.window(Group.objects.all(), _get(cursor=token, restore=1))
        assert _names(window) == NAMES[5:25]
        assert window.previous == pager.encode(("g005",), reverse=True)
        assert window.next == self._token_after(pager, "g024")
        assert window.back == token
        assert window.backs == (pager.encode(("g015",), reverse=True),)

    def test_a_reverse_batch_at_the_top_keeps_its_rows(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = pager.encode(("g005",), reverse=True)
        window = pager.window(Group.objects.all(), _get(cursor=token))
        assert _names(window) == NAMES[:5]
        assert (window.previous, window.back, window.backs) == (None, token, ())

    def test_a_reverse_token_before_every_row_is_empty(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        token = pager.encode(("a",), reverse=True)
        window = pager.window(Group.objects.all(), _get(cursor=token))
        assert (list(window), window.next, window.back) == ([], None, None)

    def test_a_forward_token_past_every_row_is_empty(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        window = pager.window(
            Group.objects.all(), _get(cursor=self._token_after(pager, "z"))
        )
        assert (list(window), window.previous) == ([], None)


@pytest.mark.django_db()
class TestKeysetTokens:
    """A token that does not decode to a position falls back to the first window."""

    @pytest.mark.parametrize("case", MALFORMED_TOKENS, ids=lambda c: c.id)
    def test_a_malformed_token_reads_the_first_window(
        self, groups: None, case: TokenCase
    ) -> None:
        pager = KeysetPager(("name", "pk"), per_page=10)
        window = pager.window(Group.objects.all(), _get(cursor=case.token, restore=1))
        assert _names(window) == NAMES[:10]
        assert window.previous is None

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("2026-03-01T12:00:00", id="naive"),
            pytest.param("not a date", id="text"),
            pytest.param(5, id="int"),
        ],
    )
    def test_a_bad_datetime_reads_the_first_window(self, users, value: object) -> None:
        pager = KeysetPager(("-date_joined", "pk"), per_page=4)
        window = pager.window(
            User.objects.all(), _get(cursor=raw_token([False, value, 1]))
        )
        first = pager.window(User.objects.all(), None)
        assert [user.pk for user in window] == [user.pk for user in first]

    def test_a_datetime_must_match_the_time_zone_setting(self) -> None:
        pager = KeysetPager(("-date_joined", "pk"))
        shape = pager._shape(User)
        aware = raw_token([False, "2026-03-01T12:00:00.000001+00:00", 1])
        naive = raw_token([False, "2026-03-01T12:00:00.000001", 1])
        moment = MOMENT.replace(microsecond=1)
        assert pager._position(aware, shape) == (False, (moment, 1))
        assert pager._position(naive, shape) is None
        with override_settings(USE_TZ=False):
            assert pager._position(aware, shape) is None
            assert pager._position(naive, shape) == (
                False,
                (moment.replace(tzinfo=None), 1),
            )

    def test_a_long_position_is_refused_when_written(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10, max_token=8)
        with pytest.raises(ValueError, match="max_token of 8"):
            pager.window(Group.objects.all(), None)

    def test_a_subclass_swaps_the_codec(self, groups: None) -> None:
        class HexPager(KeysetPager):
            def encode(self, values, *, reverse: bool) -> str:
                return ("r" if reverse else "f") + values[0].encode().hex()

            def decode(self, token: str):
                return token[0] == "r", (bytes.fromhex(token[1:]).decode(),)

        pager = HexPager(("name",), per_page=10)
        first = pager.window(Group.objects.all(), None)
        assert first.next == "f" + b"g009".hex()
        second = pager.window(Group.objects.all(), _get(cursor=first.next))
        assert _names(second) == NAMES[10:20]


class TestKeysetValues:
    """Datetime, decimal, uuid and duration positions round-trip exactly."""

    @pytest.mark.parametrize(
        "ordering",
        [
            pytest.param(("stamp", "pk"), id="microseconds"),
            pytest.param(("-amount", "pk"), id="decimal"),
            pytest.param(("ref",), id="uuid"),
            pytest.param(("span", "-pk"), id="duration"),
        ],
    )
    def test_every_row_is_walked_once(self, ledger, ordering: tuple[str, ...]) -> None:
        ledger.objects.bulk_create(
            ledger(
                stamp=MOMENT + timedelta(microseconds=index * 7 // 2),
                amount=Decimal(index // 2) / 4,
                ref=UUID(int=(index * 7919) % 2**32),
                span=timedelta(seconds=index // 3, microseconds=index),
            )
            for index in range(20)
        )
        pager = KeysetPager(ordering, per_page=3)
        windows = _forward(pager, ledger.objects.all())
        walked = [row.pk for window in windows for row in window]
        expected = list(ledger.objects.order_by(*ordering).values_list("pk", flat=True))
        assert walked == expected
        back = _backward(pager, ledger.objects.all(), windows[-1].previous)
        assert [row.pk for window in reversed(back) for row in window] == expected[:18]

    def test_a_nullable_column_is_refused(self, ledger) -> None:
        ledger.objects.create(stamp=MOMENT, amount=1, ref=UUID(int=1), span=timedelta())
        with pytest.raises(ImproperlyConfigured, match="nullable 'rank'"):
            KeysetPager(("rank", "pk")).window(ledger.objects.all(), None)


@pytest.mark.django_db()
class TestKeysetOrdering:
    """The ordering must name local non-null columns and end in a unique one."""

    @pytest.mark.parametrize(
        ("ordering", "message"),
        [
            pytest.param("pk", "non-empty sequence", id="string"),
            pytest.param((), "non-empty sequence", id="empty"),
            pytest.param(("a b",), "no field name", id="not-a-name"),
            pytest.param(("--pk",), "no field name", id="double-minus"),
            pytest.param(("?",), "no field name", id="random"),
            pytest.param(("pk", "-pk"), "twice", id="twice"),
        ],
    )
    def test_a_bad_shape_is_refused_at_once(
        self, ordering: object, message: str
    ) -> None:
        with pytest.raises(ValueError, match=message):
            KeysetPager(ordering)

    @pytest.mark.parametrize("max_token", [0, True], ids=["zero", "bool"])
    def test_a_bad_token_cap_is_refused(self, max_token: int) -> None:
        with pytest.raises(ValueError, match="max_token"):
            KeysetPager(("pk",), max_token=max_token)

    @pytest.mark.parametrize(
        ("model", "ordering", "message"),
        [
            pytest.param(Group, ("nope",), "no local column", id="missing"),
            pytest.param(Group, ("user", "pk"), "no local column", id="reverse"),
            pytest.param(
                Permission, ("content_type", "pk"), "no local column", id="relation"
            ),
            pytest.param(User, ("last_login", "pk"), "nullable", id="nullable"),
            pytest.param(User, ("date_joined",), "not unique", id="not-unique"),
            pytest.param(User, ("pk", "id"), "twice", id="pk-and-id"),
        ],
    )
    def test_a_column_the_model_cannot_page_by_is_refused(
        self, model: type[Model], ordering: tuple[str, ...], message: str
    ) -> None:
        with pytest.raises(ImproperlyConfigured, match=message):
            KeysetPager(ordering).window(model.objects.all(), None)

    def test_a_foreign_key_pages_by_its_attname(self) -> None:
        pager = KeysetPager(("content_type_id", "pk"), per_page=5)
        windows = _forward(pager, Permission.objects.all())
        walked = [row.pk for window in windows for row in window]
        assert walked == list(
            Permission.objects.order_by("content_type_id", "pk").values_list(
                "pk", flat=True
            )
        )

    def test_the_ordering_is_settled_once_per_model(
        self, groups: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[type[Model]] = []
        settle = paging_module._settle

        def counted(model, order):
            calls.append(model)
            return settle(model, order)

        monkeypatch.setattr(paging_module, "_settle", counted)
        pager = KeysetPager(("name",))
        pager.window(Group.objects.all(), None)
        pager.window(Group.objects.all(), None)
        assert calls == [Group]
        assert pager.ordering == ("name",)


@pytest.mark.django_db()
class TestKeysetSequence:
    """A sequence of model instances in the ordering pages without a query."""

    def test_it_walks_like_the_queryset(self, users, django_assert_num_queries) -> None:
        pager = KeysetPager(("-date_joined", "username"), per_page=4)
        rows = list(User.objects.order_by("-date_joined", "username"))
        with django_assert_num_queries(0):
            windows = _forward(pager, rows)
            back = _backward(pager, rows, windows[-1].previous)
        expected = [user.username for user in rows]
        assert [user.username for window in windows for user in window] == expected
        walked = [user.username for window in reversed(back) for user in window]
        assert walked == expected[:24]

    def test_a_restore_matches_the_queryset(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=25)
        token = pager.encode(("g059",), reverse=False)
        request = _get(cursor=token, restore=1)
        listed = pager.window(list(Group.objects.order_by("name")), request)
        queried = pager.window(Group.objects.all(), request)
        assert _names(listed) == _names(queried)
        assert (listed.backs, listed.previous) == (queried.backs, queried.previous)

    def test_a_count_is_its_length(self, groups: None) -> None:
        rows = list(Group.objects.order_by("name"))
        window = KeysetPager(("name",), count=True).window(rows, None)
        assert window.count == 95

    def test_an_empty_sequence_is_one_empty_window(self) -> None:
        window = KeysetPager(("name",), count=True).window([], None)
        assert (list(window), window.next, window.count) == ([], None, 0)

    def test_rows_that_are_no_model_instances_are_refused(self) -> None:
        with pytest.raises(TypeError, match="sequence of int"):
            KeysetPager(("name",)).window([1, 2], None)


@pytest.mark.django_db()
class TestKeysetAsync:
    """The async window reads the same rows through the async queryset API."""

    def test_the_first_window(self, groups: None, django_assert_num_queries) -> None:
        pager = KeysetPager(("name",), per_page=10, count=True)
        with django_assert_num_queries(2):
            window = async_to_sync(pager.awindow)(Group.objects.all(), None)
        assert window == pager.window(Group.objects.all(), None)
        assert window.count == 95

    def test_a_restore(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=25)
        request = _get(cursor=pager.encode(("g059",), reverse=False), restore=1)
        window = async_to_sync(pager.awindow)(Group.objects.all(), request)
        assert window == pager.window(Group.objects.all(), request)

    def test_a_reverse_batch(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        request = _get(cursor=pager.encode(("g030",), reverse=True))
        window = async_to_sync(pager.awindow)(Group.objects.all(), request)
        assert _names(window) == NAMES[20:30]

    def test_a_sequence(self, groups: None) -> None:
        pager = KeysetPager(("name",), per_page=10)
        rows = list(Group.objects.order_by("name"))
        window = async_to_sync(pager.awindow)(rows, None)
        assert _names(window) == NAMES[:10]

    def test_paging_awaits_the_pager(self, groups: None) -> None:
        paging = Paging(KeysetPager(("name",), per_page=10), None)
        window = async_to_sync(paging.apaginate)(Group.objects.all())
        assert window == paging.paginate(Group.objects.all())


LEDGER = KeysetPager(("name",))
NUMBERS = NumberPager(per_page=5)


class TestPagerOf:
    """`pager_of` reads the one pager an `Annotated[Paging, ...]` names."""

    @pytest.mark.parametrize(
        ("annotation", "pager"),
        [
            pytest.param(Annotated[Paging, LEDGER], LEDGER, id="pager"),
            pytest.param(Annotated[Paging, "doc", NUMBERS], NUMBERS, id="with-doc"),
            pytest.param(Annotated[Paging, LEDGER, NUMBERS], None, id="two"),
            pytest.param(Annotated[Paging, "doc"], None, id="none"),
            pytest.param(Annotated[int, LEDGER], None, id="other-type"),
            pytest.param(Paging, None, id="bare"),
            pytest.param("Paging", None, id="string"),
        ],
    )
    def test_pager_of(self, annotation: object, pager: Pager | None) -> None:
        assert pager_of(annotation) is pager


class TestPagingProvider:
    """The provider fills `Annotated[Paging, pager]` from the request without blocking."""

    def test_it_never_blocks(self) -> None:
        param = inspect_parameter("page", Annotated[Paging, LEDGER])
        assert PagingProvider().static_blocking(param) is False

    def test_it_claims_every_paging_annotation(self) -> None:
        provider = PagingProvider()
        context = _context(None)
        for annotation in (Annotated[Paging, LEDGER], Paging):
            param = inspect_parameter("page", annotation)
            assert provider.static_can_handle(param) is True
            assert provider.can_handle(param, context) is True
        assert provider.static_can_handle(inspect_parameter("page", int)) is False

    def test_resolve_binds_the_request(self) -> None:
        request = _get(cursor="x")
        param = inspect_parameter("page", Annotated[Paging, LEDGER])
        assert PagingProvider().resolve(param, _context(request)) == Paging(
            LEDGER, request
        )

    @pytest.mark.parametrize(
        "annotation", [Paging, Annotated[Paging, LEDGER, NUMBERS]], ids=["bare", "two"]
    )
    def test_a_paging_without_one_pager_is_refused(self, annotation: object) -> None:
        param = inspect_parameter("page", annotation)
        with pytest.raises(TypeError, match="without exactly one pager"):
            PagingProvider().compile_resolve(param)

    def test_a_context_callable_receives_the_paging(self) -> None:
        def rows(page: Annotated[Paging, NUMBERS]) -> Window[int]:
            return page.paginate(ROWS)

        request = _get(page=3)
        resolved = resolver.resolve_dependencies(
            rows, request=request, _context_data={"page": "shadow"}
        )
        assert resolved == {"page": Paging(NUMBERS, request)}
        assert list(rows(**resolved)) == list(range(10, 15))
