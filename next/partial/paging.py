"""Pagers that cut the rows of a `{% more %}` list into windows, by number or keyset."""

import json
import math
import re
from abc import ABC, abstractmethod
from bisect import bisect_left, bisect_right
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, tzinfo
from functools import partial
from typing import (
    TYPE_CHECKING,
    Annotated,
    Any,
    Final,
    get_args,
    get_origin,
    overload,
    override,
)

from django.conf import settings
from django.core.exceptions import (
    FieldDoesNotExist,
    ImproperlyConfigured,
    ValidationError,
)
from django.core.serializers.json import DjangoJSONEncoder
from django.db import connections
from django.db.models import Field, Model, Q, QuerySet
from django.http import HttpRequest
from django.utils import timezone
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode

from next.deps import RegisteredParameterProvider
from next.deps.markers import unwrap_annotated


if TYPE_CHECKING:
    import inspect

    from next.deps import ParameterFiller, ResolutionContext


DEFAULT_PARAM: Final = "page"
CURSOR_PARAM: Final = "cursor"
DEFAULT_PER_PAGE: Final = 20
RESTORE_PARAM: Final = "_restore"
"""The query parameter a restored window carries, valued with the name of its pager."""
MAX_TOKEN: Final = 512
MAX_RESTORE: Final = 10
"""The most windows a restored list renders back at once, unless its pager says."""

_SLUG: Final = re.compile(r"[A-Za-z0-9_-]+")
_DIGITS: Final = re.compile(r"[0-9]+")
_MAX_DIGITS: Final = 19
_MAX_OFFSET: Final = 2**63 - 2

type PagedRows[T] = QuerySet[Any, T] | Sequence[T]
type _Finish[T] = Callable[[list[list[T]], int | None], Window[T] | WindowPlan[T]]
type _Order = tuple[tuple[str, bool], ...]


def valid_param(param: object) -> bool:
    """Return whether a query parameter name is a slug the window does not reserve."""
    return (
        isinstance(param, str)
        and _SLUG.fullmatch(param) is not None
        and param != RESTORE_PARAM
    )


@dataclass(frozen=True, slots=True)
class Window[T]:
    """The rows one render of a list shows and the tokens of the windows around them.

    `back` names the window just before the batch `next` fetches, `backs` inner ones.
    """

    rows: Sequence[T]
    param: str
    next: str | None = None
    previous: str | None = None
    back: str | None = None
    backs: tuple[str, ...] = ()
    count: int | None = None

    def __iter__(self) -> Iterator[T]:
        """Iterate the rows, so a template loops over the window itself."""
        return iter(self.rows)

    def __len__(self) -> int:
        """Return how many rows the window holds."""
        return len(self.rows)

    @overload
    def __getitem__(self, index: int) -> T: ...

    @overload
    def __getitem__(self, index: slice) -> Sequence[T]: ...

    def __getitem__(self, index: int | slice) -> T | Sequence[T]:
        """Return a row or a slice of the rows, so `first`, `last` and `slice` work."""
        return self.rows[index]

    def __reversed__(self) -> Iterator[T]:
        """Iterate the rows backwards, so a template loops over them reversed."""
        return reversed(self.rows)

    def has_next(self) -> bool:
        """Return whether a window follows, as `Page.has_next` of Django answers."""
        return self.next is not None

    def has_previous(self) -> bool:
        """Return whether a window comes before, as `Page.has_previous` answers."""
        return self.previous is not None


def _query(request: HttpRequest | None, param: str) -> tuple[str | None, bool]:
    """Return the raw token the request names under `param` and its restore flag.

    The flag counts only when it names `param`, so another pager's flag reads one page.
    """
    if request is None:
        return None, False
    query = request.GET
    return query.get(param), query.get(RESTORE_PARAM) == param


async def _alist[T](rows: PagedRows[T]) -> list[T]:
    """Return the rows of a slice, a queryset read through its async iterator."""
    if isinstance(rows, QuerySet):
        return [row async for row in rows]
    return list(rows)


def _count[T](rows: PagedRows[T]) -> int:
    """Return how many rows the source holds."""
    return rows.count() if isinstance(rows, QuerySet) else len(rows)


async def _acount[T](rows: PagedRows[T]) -> int:
    """Return how many rows the source holds, a queryset counted without blocking."""
    return await rows.acount() if isinstance(rows, QuerySet) else len(rows)


@dataclass(frozen=True, slots=True)
class WindowPlan[T]:
    """The bounded slices one window reads and the step that builds it from them.

    Planning reads nothing, and a step that needs one more read answers its plan.
    """

    slices: tuple[PagedRows[T], ...]
    total: PagedRows[T] | None
    finish: _Finish[T]

    def run(self) -> Window[T]:
        """Read the slices and the asked count, then build the window."""
        plan: WindowPlan[T] = self
        while True:
            fetched = [list(part) for part in plan.slices]
            count = None if plan.total is None else _count(plan.total)
            built = plan.finish(fetched, count)
            if isinstance(built, Window):
                return built
            plan = built

    async def arun(self) -> Window[T]:
        """Read the slices and the asked count without blocking, then build it."""
        plan: WindowPlan[T] = self
        while True:
            fetched = [await _alist(part) for part in plan.slices]
            count = None if plan.total is None else await _acount(plan.total)
            built = plan.finish(fetched, count)
            if isinstance(built, Window):
                return built
            plan = built


class Pager(ABC):
    """Cut the rows of a list into the window the query string of a request asks for.

    A pager keeps no request state, so one module-level instance serves every render.
    """

    __slots__ = ("count", "max_restore", "param", "per_page")

    def __init__(
        self, *, param: str, per_page: int, count: bool, max_restore: int = MAX_RESTORE
    ) -> None:
        """Store the options, refusing a reserved parameter or a bound below 1."""
        if not valid_param(param):
            msg = (
                "A pager takes its query parameter as a slug other than "
                f'"{RESTORE_PARAM}", got {param!r}.'
            )
            raise ValueError(msg)
        if isinstance(per_page, bool) or per_page < 1:
            msg = f"A pager takes per_page as an integer from 1, got {per_page!r}."
            raise ValueError(msg)
        if isinstance(max_restore, bool) or max_restore < 1:
            msg = (
                f"A pager takes max_restore as an integer from 1, got {max_restore!r}."
            )
            raise ValueError(msg)
        self.max_restore = max_restore
        self.param = param
        self.per_page = per_page
        self.count = count

    @abstractmethod
    def plan[T](self, rows: PagedRows[T], request: HttpRequest | None) -> WindowPlan[T]:
        """Return the slices the window of `rows` the request asks for reads."""

    def window[T](self, rows: PagedRows[T], request: HttpRequest | None) -> Window[T]:
        """Return the window of `rows` the request asks for, read synchronously."""
        return self.plan(rows, request).run()

    async def awindow[T](
        self, rows: PagedRows[T], request: HttpRequest | None
    ) -> Window[T]:
        """Return the window of `rows` the request asks for, a queryset awaited."""
        return await self.plan(rows, request).arun()


class NumberPager(Pager):
    """Page by `?page=N`, a restore reaching back `max_restore` pages from page N."""

    __slots__ = ("_ceiling",)

    def __init__(
        self,
        *,
        param: str = DEFAULT_PARAM,
        per_page: int = DEFAULT_PER_PAGE,
        count: bool = False,
        max_restore: int = MAX_RESTORE,
    ) -> None:
        """Store the options and the last page whose rows a database can address."""
        super().__init__(
            param=param, per_page=per_page, count=count, max_restore=max_restore
        )
        self._ceiling = _MAX_OFFSET // per_page

    def _number(self, raw: str | None) -> int:
        """Return the page a query value names, 1 for anything but a reachable page."""
        if raw is None or len(raw) > _MAX_DIGITS or _DIGITS.fullmatch(raw) is None:
            return 1
        number = int(raw)
        return number if 1 <= number <= self._ceiling else 1

    @override
    def plan[T](self, rows: PagedRows[T], request: HttpRequest | None) -> WindowPlan[T]:
        """Return the one slice a run of pages reads, one row past its end."""
        raw, restore = _query(request, self.param)
        last = self._number(raw)
        first = last
        if restore:
            first = max(1, last - self.max_restore + 1)
        per_page = self.per_page
        part = rows[(first - 1) * per_page : last * per_page + 1]
        total = rows if self.count else None
        finish: _Finish[T] = partial(self._finish, rows, first, last)
        return WindowPlan((part,), total, finish)

    def _finish[T](
        self,
        rows: PagedRows[T],
        first: int,
        last: int,
        fetched: list[list[T]],
        count: int | None,
    ) -> Window[T] | WindowPlan[T]:
        """Return the window of pages `first` to `last`, cut short at the end.

        A run past the last page counts the rows, so its `previous` names the last one.
        """
        [found] = fetched
        if not found and first > 1:
            if count is not None:
                return self._beyond(first, [], count, counted=True)
            return WindowPlan((), rows, partial(self._beyond, first, counted=False))
        per_page = self.per_page
        size = (last - first + 1) * per_page
        more = len(found) > size
        shown = found[:size]
        end = last if more else first + max(0, len(shown) - 1) // per_page
        return Window(
            rows=shown,
            param=self.param,
            next=str(end + 1) if more else None,
            previous=str(first - 1) if first > 1 and shown else None,
            back=str(end) if more else None,
            backs=tuple(str(number) for number in range(first, end)),
            count=count,
        )

    def _beyond[T](
        self, first: int, _fetched: list[list[T]], count: int | None, *, counted: bool
    ) -> Window[T]:
        """Return the empty window past the end, `previous` naming the last page."""
        held = count or 0
        last = min(first - 1, -(-held // self.per_page))
        return Window(
            rows=[],
            param=self.param,
            previous=str(last) if last >= 1 else None,
            count=count if counted else None,
        )


class _PositionEncoder(DjangoJSONEncoder):
    """Encode a position, keeping the microseconds `DjangoJSONEncoder` rounds away."""

    @override
    def default(self, o: object) -> object:
        """Write a datetime or a time in full ISO form, the rest as Django does."""
        if isinstance(o, datetime | time):
            return o.isoformat()
        return super().default(o)


@dataclass(frozen=True, slots=True)
class _Column:
    """One column of a keyset ordering and the strict and loose lookups seeking it."""

    attname: str
    field: "Field[Any, Any]"
    forward: tuple[str, str]
    backward: tuple[str, str]


class _Key:
    """The ordering key of a row held in memory, compared column by column."""

    __slots__ = ("descending", "values")

    def __init__(self, values: tuple[Any, ...], descending: tuple[bool, ...]) -> None:
        """Store the value of each column and its direction."""
        self.values = values
        self.descending = descending

    def __lt__(self, other: "_Key") -> bool:
        """Return whether this key sorts before `other` under the ordering."""
        for mine, theirs, descending in zip(
            self.values, other.values, self.descending, strict=True
        ):
            if mine != theirs:
                return bool(mine > theirs if descending else mine < theirs)
        return False


@dataclass(frozen=True, slots=True)
class _Shape:
    """The ordering of a keyset pager settled against one model."""

    columns: tuple[_Column, ...]
    forward: tuple[str, ...]
    backward: tuple[str, ...]
    descending: tuple[bool, ...]

    def values(self, row: object) -> tuple[Any, ...]:
        """Return the position of `row`, the value of each column."""
        return tuple(getattr(row, column.attname) for column in self.columns)

    def key(self, row: object) -> _Key:
        """Return the ordering key of a row held in memory."""
        return _Key(self.values(row), self.descending)


def _ordering(ordering: Sequence[str]) -> _Order:
    """Return each column name of an ordering and its direction, refusing bad shapes."""
    if isinstance(ordering, str) or not ordering:
        msg = (
            "KeysetPager takes its ordering as a non-empty sequence of field names, "
            f"as in ('-created', 'pk'), got {ordering!r}."
        )
        raise ValueError(msg)
    parsed: list[tuple[str, bool]] = []
    for entry in ordering:
        name = entry.removeprefix("-")
        if not name.isidentifier():
            msg = f"KeysetPager ordering names {entry!r}, which is no field name."
            raise ValueError(msg)
        if any(name == seen for seen, _descending in parsed):
            msg = f"KeysetPager ordering names {name!r} twice."
            raise ValueError(msg)
        parsed.append((name, entry != name))
    return tuple(parsed)


def _column(model: type[Model], name: str) -> "Field[Any, Any]":
    """Return the non-null local column `name` names on `model`."""
    meta = model._meta
    try:
        found = meta.pk if name == "pk" else meta.get_field(name)
    except FieldDoesNotExist:
        found = None
    if (
        not isinstance(found, Field)
        or not found.concrete
        or (found.is_relation and name not in {"pk", found.attname})
    ):
        msg = (
            f"KeysetPager orders {meta.label} by {name!r}, which is no local column. "
            "Name a concrete field, and a foreign key by its attname, as in author_id."
        )
        raise ImproperlyConfigured(msg)
    if found.null:
        msg = (
            f"KeysetPager orders {meta.label} by the nullable {name!r}, and a NULL "
            "never compares, so its rows would never page. Order by non-null columns."
        )
        raise ImproperlyConfigured(msg)
    return found


def _settle(model: type[Model], order: _Order) -> _Shape:
    """Return the ordering settled against `model`, ending in a unique column."""
    label = model._meta.label
    columns: list[_Column] = []
    forward_order: list[str] = []
    backward_order: list[str] = []
    for name, descending in order:
        field = _column(model, name)
        attname = field.attname
        if any(column.attname == attname for column in columns):
            msg = f"KeysetPager orders {label} by {attname!r} twice."
            raise ImproperlyConfigured(msg)
        rising = (f"{attname}__gt", f"{attname}__gte")
        falling = (f"{attname}__lt", f"{attname}__lte")
        forward, backward = (falling, rising) if descending else (rising, falling)
        columns.append(_Column(attname, field, forward, backward))
        forward_order.append(f"-{attname}" if descending else attname)
        backward_order.append(attname if descending else f"-{attname}")
    tail = columns[-1]
    if not tail.field.unique:
        msg = (
            f"KeysetPager orders {label} ending in {tail.attname!r}, which is not "
            "unique, so rows tied on every column would be skipped or repeated. End "
            "the ordering in a unique column such as pk."
        )
        raise ImproperlyConfigured(msg)
    return _Shape(
        columns=tuple(columns),
        forward=tuple(forward_order),
        backward=tuple(backward_order),
        descending=tuple(descending for _name, descending in order),
    )


def _scalar(value: object) -> bool:
    """Return whether a decoded token value is a finite JSON scalar."""
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, str | int)


def _storable(value: object) -> bool:
    """Return whether a converted value is one a non-null column holds and compares."""
    if isinstance(value, str):
        return "\x00" not in value
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, datetime):
        return timezone.is_aware(value) == settings.USE_TZ
    return value is not None


def _typed(
    columns: tuple[_Column, ...], raw: tuple[object, ...], zone: tzinfo | None
) -> tuple[Any, ...] | None:
    """Return the token values converted by their fields, `None` when one is refused.

    An aware datetime moves into the database zone, so one that overflows is refused.
    """
    values: list[object] = []
    for column, value in zip(columns, raw, strict=True):
        field = column.field
        try:
            typed = field.to_python(value)
            field.run_validators(typed)
            if isinstance(typed, datetime) and timezone.is_aware(typed):
                typed = typed.astimezone(zone)
        except (ValidationError, TypeError, ValueError, OverflowError):
            return None
        if not _storable(typed):
            return None
        values.append(typed)
    return tuple(values)


def _zone[T](rows: PagedRows[T]) -> tzinfo | None:
    """Return the zone the database of a queryset reads datetimes in, UTC in memory."""
    return connections[rows.db].timezone if isinstance(rows, QuerySet) else UTC


def _seek_filter(
    columns: tuple[_Column, ...],
    values: tuple[Any, ...],
    *,
    backward: bool,
    inclusive: bool,
) -> Q:
    """Return the filter of the rows past a position, an OR chain over the ordering.

    The bound on the leading column repeats the first step, so an index serves it.
    """
    chain = Q()
    equal: dict[str, object] = {}
    last = len(columns) - 1
    for index, (column, value) in enumerate(zip(columns, values, strict=True)):
        strict, loose = column.backward if backward else column.forward
        lookup = loose if inclusive and index == last else strict
        chain |= Q(**equal, **{lookup: value})
        equal[column.attname] = value
    if last:
        lead = columns[0]
        chain &= Q(**{(lead.backward if backward else lead.forward)[1]: values[0]})
    return chain


def _head[T](rows: PagedRows[T], shape: _Shape, limit: int) -> PagedRows[T]:
    """Return the first `limit` rows under the ordering."""
    if isinstance(rows, QuerySet):
        return rows.order_by(*shape.forward)[:limit]
    return rows[:limit]


def _seek[T](
    rows: PagedRows[T],
    shape: _Shape,
    values: tuple[Any, ...],
    *,
    backward: bool,
    limit: int,
    inclusive: bool = False,
) -> PagedRows[T]:
    """Return up to `limit` rows past a position, nearest first.

    A queryset is filtered and ordered, a sequence already in the ordering bisected.
    """
    if isinstance(rows, QuerySet):
        seek = _seek_filter(
            shape.columns, values, backward=backward, inclusive=inclusive
        )
        order = shape.backward if backward else shape.forward
        return rows.filter(seek).order_by(*order)[:limit]
    probe = _Key(values, shape.descending)
    if backward:
        bisect = bisect_right if inclusive else bisect_left
        end = bisect(rows, probe, key=shape.key)
        return rows[max(0, end - limit) : end][::-1]
    start = bisect_right(rows, probe, key=shape.key)
    return rows[start : start + limit]


def _model_of(row: object) -> type[Model]:
    """Return the model of a row of a sequence, refusing anything but an instance."""
    if not isinstance(row, Model):
        msg = (
            "KeysetPager pages a queryset or a sequence of model instances, got a "
            f"sequence of {type(row).__name__}."
        )
        raise TypeError(msg)
    return type(row)


class KeysetPager(Pager):
    """Page by `?cursor=<token>`, the position of a row under a total ordering.

    `encode` and `decode` are the points a subclass swaps to sign or shorten tokens.
    """

    __slots__ = ("_order", "_shapes", "max_token", "ordering")

    def __init__(
        self,
        ordering: Sequence[str],
        *,
        param: str = CURSOR_PARAM,
        per_page: int = DEFAULT_PER_PAGE,
        count: bool = False,
        max_token: int = MAX_TOKEN,
        max_restore: int = MAX_RESTORE,
    ) -> None:
        """Store the ordering, refusing one that is empty or names a column twice."""
        super().__init__(
            param=param, per_page=per_page, count=count, max_restore=max_restore
        )
        self._order = _ordering(ordering)
        if isinstance(max_token, bool) or max_token < 1:
            msg = (
                f"KeysetPager takes max_token as an integer from 1, got {max_token!r}."
            )
            raise ValueError(msg)
        self.ordering = tuple(ordering)
        self.max_token = max_token
        self._shapes: dict[type[Model], _Shape] = {}

    def encode(self, values: tuple[Any, ...], *, reverse: bool) -> str:
        """Return the token of a position, unpadded base64url of compact JSON."""
        data = json.dumps(
            [reverse, *values], cls=_PositionEncoder, separators=(",", ":")
        )
        return urlsafe_base64_encode(data.encode())

    def decode(self, token: str) -> tuple[bool, tuple[object, ...]] | None:
        """Return the direction and the raw values of a token, `None` if malformed."""
        if _SLUG.fullmatch(token) is None:
            return None
        try:
            payload = json.loads(urlsafe_base64_decode(token))
        except ValueError:
            return None
        if not isinstance(payload, list) or not payload:
            return None
        reverse, *values = payload
        if not isinstance(reverse, bool) or not all(map(_scalar, values)):
            return None
        return reverse, tuple(values)

    def _shape(self, model: type[Model]) -> _Shape:
        """Return the ordering settled against `model`, checked once per model."""
        shape = self._shapes.get(model)
        if shape is None:
            shape = self._shapes[model] = _settle(model, self._order)
        return shape

    def _bounded(self, values: tuple[Any, ...], *, reverse: bool) -> str:
        """Return the token of a position, refusing one too long to be read back."""
        token = self.encode(values, reverse=reverse)
        if len(token) > self.max_token:
            msg = (
                f"KeysetPager wrote a token of {len(token)} characters, past its "
                f"max_token of {self.max_token}. Order by shorter columns or raise "
                "max_token."
            )
            raise ValueError(msg)
        return token

    def _token(self, shape: _Shape, row: object, *, reverse: bool) -> str:
        """Return the token of the position of `row`."""
        return self._bounded(shape.values(row), reverse=reverse)

    def _position(
        self, raw: str, shape: _Shape, zone: tzinfo | None
    ) -> tuple[bool, tuple[Any, ...]] | None:
        """Return the direction and the typed position of a token, `None` if refused."""
        if len(raw) > self.max_token:
            return None
        decoded = self.decode(raw)
        if decoded is None or len(decoded[1]) != len(shape.columns):
            return None
        values = _typed(shape.columns, decoded[1], zone)
        return None if values is None else (decoded[0], values)

    @override
    def plan[T](self, rows: PagedRows[T], request: HttpRequest | None) -> WindowPlan[T]:
        """Return the slices the batch the token names reads, or the first batch."""
        total = rows if self.count else None
        if isinstance(rows, QuerySet):
            model: type[Model] = rows.model
        elif rows:
            model = _model_of(rows[0])
        else:
            return WindowPlan((), total, self._empty)
        shape = self._shape(model)
        raw, restore = _query(request, self.param)
        position = None if raw is None else self._position(raw, shape, _zone(rows))
        per_page = self.per_page
        finish: _Finish[T]
        if position is None:
            finish = partial(self._first, shape)
            return WindowPlan((_head(rows, shape, per_page + 1),), total, finish)
        reverse, values = position
        reach = self.max_restore if restore else 1
        if reverse:
            limit = reach * per_page
            part = _seek(rows, shape, values, backward=True, limit=limit + 1)
            finish = partial(self._backward, shape, values, limit)
            return WindowPlan((part,), total, finish)
        after = _seek(rows, shape, values, backward=False, limit=per_page + 1)
        if reach == 1:
            return WindowPlan((after,), total, partial(self._forward, shape))
        limit = (reach - 1) * per_page
        before = _seek(
            rows, shape, values, backward=True, inclusive=True, limit=limit + 1
        )
        finish = partial(self._restored, shape, limit)
        return WindowPlan((after, before), total, finish)

    def _empty[T](self, _fetched: list[list[T]], count: int | None) -> Window[T]:
        """Return the window of a source with no row."""
        return Window(rows=[], param=self.param, count=count)

    def _ahead[T](self, shape: _Shape, after: list[T]) -> tuple[str | None, str | None]:
        """Return the token of the batch after `after` and of the window before it.

        The row read past the batch holds the position the back token names.
        """
        per_page = self.per_page
        if len(after) <= per_page:
            return None, None
        return (
            self._token(shape, after[per_page - 1], reverse=False),
            self._token(shape, after[per_page], reverse=True),
        )

    def _backs[T](self, shape: _Shape, shown: list[T]) -> tuple[str, ...]:
        """Return the back token of every batch of `shown` after its first."""
        return tuple(
            self._token(shape, shown[index], reverse=True)
            for index in range(self.per_page, len(shown), self.per_page)
        )

    def _first[T](
        self, shape: _Shape, fetched: list[list[T]], count: int | None
    ) -> Window[T]:
        """Return the first batch, one row past it read."""
        [head] = fetched
        following, back = self._ahead(shape, head)
        return Window(
            rows=head[: self.per_page],
            param=self.param,
            next=following,
            back=back,
            count=count,
        )

    def _forward[T](
        self, shape: _Shape, fetched: list[list[T]], count: int | None
    ) -> Window[T]:
        """Return the batch after a position, which leaves rows before it."""
        [after] = fetched
        shown = after[: self.per_page]
        following, back = self._ahead(shape, after)
        return Window(
            rows=shown,
            param=self.param,
            next=following,
            previous=self._token(shape, shown[0], reverse=True) if shown else None,
            back=back,
            count=count,
        )

    def _restored[T](
        self, shape: _Shape, limit: int, fetched: list[list[T]], count: int | None
    ) -> Window[T]:
        """Return the batch after a position under the whole batches before it.

        A window that grows holds whole batches only, the rest left to `previous`.
        """
        after, before = fetched
        per_page = self.per_page
        following, back = self._ahead(shape, after)
        kept = before[:limit]
        if following is not None:
            kept = kept[: len(kept) // per_page * per_page]
        shown = kept[::-1] + after[:per_page]
        return Window(
            rows=shown,
            param=self.param,
            next=following,
            previous=self._token(shape, shown[0], reverse=True)
            if shown and len(before) > len(kept)
            else None,
            back=back,
            backs=self._backs(shape, shown) if following is not None else (),
            count=count,
        )

    def _backward[T](
        self,
        shape: _Shape,
        values: tuple[Any, ...],
        limit: int,
        fetched: list[list[T]],
        count: int | None,
    ) -> Window[T]:
        """Return the batches before a position, whole ones when more than one.

        The position held a row, so the batch after the window starts there.
        """
        [before] = fetched
        per_page = self.per_page
        kept = before[:limit]
        whole = len(kept) // per_page * per_page
        if whole:
            kept = kept[:whole]
        shown = kept[::-1]
        return Window(
            rows=shown,
            param=self.param,
            next=self._token(shape, shown[-1], reverse=False) if shown else None,
            previous=self._token(shape, shown[0], reverse=True)
            if len(before) > len(kept)
            else None,
            back=self._bounded(values, reverse=True) if shown else None,
            backs=self._backs(shape, shown),
            count=count,
        )


@dataclass(frozen=True, slots=True)
class Paging:
    """A pager bound to the request of one render, injected by its `Annotated` form."""

    pager: Pager
    request: HttpRequest | None

    def paginate[T](self, rows: PagedRows[T]) -> Window[T]:
        """Return the window of `rows` the request asks for."""
        return self.pager.window(rows, self.request)

    async def apaginate[T](self, rows: PagedRows[T]) -> Window[T]:
        """Return the window of `rows` the request asks for, a queryset awaited."""
        return await self.pager.awindow(rows, self.request)


def pager_of(annotation: object) -> Pager | None:
    """Return the one pager an `Annotated[Paging, pager]` names, else `None`."""
    if get_origin(annotation) is not Annotated:
        return None
    base, *extras = get_args(annotation)
    pagers = [extra for extra in extras if isinstance(extra, Pager)]
    return pagers[0] if base is Paging and len(pagers) == 1 else None


class PagingProvider(RegisteredParameterProvider):
    """Fill `Annotated[Paging, pager]` parameters with the pager and the request.

    Priority 25 keeps a context value of the same name from shadowing the annotation.
    """

    priority = 25
    blocking = False

    @override
    def can_handle(
        self, param: "inspect.Parameter", _context: "ResolutionContext"
    ) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param)

    @override
    def static_can_handle(self, param: "inspect.Parameter") -> bool:
        """Claim every `Paging` annotation, so one naming no pager fails loudly."""
        return unwrap_annotated(param.annotation) is Paging

    @override
    def resolve(
        self, param: "inspect.Parameter", context: "ResolutionContext"
    ) -> object:
        """Fill the parameter through the filler the plan compiler builds for it."""
        return self.compile_resolve(param)(context)

    @override
    def compile_resolve(self, param: "inspect.Parameter") -> "ParameterFiller":
        """Read the pager off the annotation once per plan."""
        pager = pager_of(param.annotation)
        if pager is None:
            msg = (
                f'Parameter "{param.name}" takes a Paging without exactly one pager. '
                "Name it in the annotation, as in Annotated[Paging, ledger]."
            )
            raise TypeError(msg)

        def fill(context: "ResolutionContext") -> object:
            return Paging(pager, context.request)

        return fill


__all__ = [
    "CURSOR_PARAM",
    "DEFAULT_PARAM",
    "DEFAULT_PER_PAGE",
    "MAX_RESTORE",
    "MAX_TOKEN",
    "RESTORE_PARAM",
    "KeysetPager",
    "NumberPager",
    "PagedRows",
    "Pager",
    "Paging",
    "PagingProvider",
    "Window",
    "WindowPlan",
    "pager_of",
    "valid_param",
]
