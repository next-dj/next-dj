import math
import typing
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from django.contrib.auth.models import AbstractUser, User
from django.contrib.sessions.models import Session
from django.db.models import DateField, Model
from django.http import Http404
from django.test.utils import isolate_apps
from django.utils import timezone
from django.utils.safestring import mark_safe

from next.defer import (
    BoundaryPropsError,
    BoundarySizeError,
    BoundaryTokenError,
    BoundaryValueError,
    TokenRejection,
)
from next.defer.tokens import decode_props, encode_props, fetch
from next.defer.tokens.props import DEFAULT_LIMIT
from tests.support.tokens import request_for


_UUID = UUID("12345678-9abc-4def-8abc-def012345678")
_MOMENT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

ROUND_TRIPS = [
    (str, "text", "text"),
    (int, 7, 7),
    (int, -(2**63), -(2**63)),
    (float, 1.5, 1.5),
    (float, 3, 3.0),
    (bool, True, True),
    (bool, False, False),
    (None, None, None),
    (type(None), None, None),
    (Decimal, Decimal("1.50"), "1.50"),
    (date, date(2026, 1, 2), "2026-01-02"),
    (datetime, _MOMENT, "2026-01-02T03:04:05+00:00"),
    (datetime, _MOMENT.replace(tzinfo=None), "2026-01-02T03:04:05"),
    (UUID, _UUID, str(_UUID)),
    (list[int], [1, 2], [1, 2]),
    (list[str], ("a", "b"), ["a", "b"]),
    (int | None, None, None),
    (list[date] | None, [date(2026, 1, 2)], ["2026-01-02"]),
]


UNANNOTATED_ROUND_TRIPS = [
    "text",
    "",
    0,
    7,
    -(2**63),
    1.5,
    1.0,
    True,
    False,
    None,
    [],
    ["a", 1, 1.5, True, None],
]

UNANNOTATED_REFUSALS = [
    (Decimal("1.5"), "Decimal"),
    (date(2026, 1, 2), "date"),
    (_MOMENT, "datetime"),
    (_UUID, "UUID"),
    (User(pk=1), "User"),
    (("a",), "tuple"),
    ([["a"]], "list"),
    ([Decimal(1)], "Decimal"),
    (TokenRejection.MALFORMED, "TokenRejection"),
    (object(), "object"),
]


@pytest.fixture()
def dated() -> typing.Iterator[type[Model]]:
    """Return a model whose primary key is a date, which no token carries."""
    with isolate_apps("tests"):

        class Ledger(Model):
            day = DateField(primary_key=True)

            class Meta:
                app_label = "tests"

            def __str__(self) -> str:
                return str(self.day)

        yield Ledger


def _decoded(annotation: object, wire: object) -> object:
    return decode_props(request_for(), {"p": wire}, {"p": annotation})["p"]


def _refusal(annotation: object, wire: object) -> TokenRejection:
    with pytest.raises(BoundaryTokenError) as caught:
        _decoded(annotation, wire)
    return caught.value.reason


class TestEncodeProps:
    """``encode_props`` writes typed values as their wire form, sorted by name."""

    @pytest.mark.parametrize(("annotation", "value", "wire"), ROUND_TRIPS)
    def test_each_type_writes_its_wire_form(
        self, annotation: object, value: object, wire: object
    ) -> None:
        assert encode_props({"p": value}, {"p": annotation}) == (("p", wire),)

    def test_unannotated_props_are_sorted_by_name(self) -> None:
        assert encode_props({"b": "x", "a": "y"}, {}) == (("a", "y"), ("b", "x"))

    @pytest.mark.parametrize("value", UNANNOTATED_ROUND_TRIPS)
    def test_an_unannotated_prop_travels_as_its_own_type(self, value: object) -> None:
        wire = encode_props({"p": value}, {})[0][1]
        assert wire == value
        assert type(wire) is type(value)

    def test_unannotated_text_marked_safe_travels_as_plain_text(self) -> None:
        wire = encode_props({"p": mark_safe("<b>x</b>")}, {})[0][1]
        assert wire == "<b>x</b>"
        assert type(wire) is str

    @pytest.mark.parametrize(("value", "named"), UNANNOTATED_REFUSALS)
    def test_an_unannotated_prop_of_another_type_names_the_prop_and_type(
        self, value: object, named: str
    ) -> None:
        with pytest.raises(BoundaryPropsError, match=rf"the prop 'p' holds a {named}"):
            encode_props({"p": value}, {})

    @pytest.mark.parametrize(
        ("annotation", "value"),
        [
            (str, 1),
            (int, "1"),
            (int, True),
            (int, 1.0),
            (float, "1.5"),
            (float, True),
            (bool, 1),
            (Decimal, 1),
            (Decimal, 1.5),
            (date, _MOMENT),
            (date, "2026-01-02"),
            (datetime, date(2026, 1, 2)),
            (UUID, str(_UUID)),
            (None, 0),
            (int, None),
            (list[int], 1),
            (list[int], ["1"]),
            (User, "1"),
            (User, Session()),
        ],
    )
    def test_a_value_of_another_type_is_refused(
        self, annotation: object, value: object
    ) -> None:
        with pytest.raises(BoundaryPropsError, match="the prop 'p'"):
            encode_props({"p": value}, {"p": annotation})

    @pytest.mark.parametrize("value", [{}, {"a": 1}])
    def test_a_mapping_is_refused_under_any_annotation(self, value: object) -> None:
        with pytest.raises(BoundaryPropsError, match="carries no dict"):
            encode_props({"p": value}, {"p": str})

    @pytest.mark.parametrize(
        "annotation",
        [
            dict,
            dict[str, int],
            list,
            list[list[int]],
            list[User],
            list[int, str],
            AbstractUser,
            tuple[int],
            int | str,
            int | str | None,
            typing.Any,
            object,
            bytes,
            "int",
        ],
    )
    def test_an_annotation_no_token_carries_is_refused(
        self, annotation: object
    ) -> None:
        refusal = r"which no token carries\. .*list\[X\] of those.* as X \| None\.$"
        with pytest.raises(BoundaryPropsError, match=refusal):
            encode_props({"p": "x"}, {"p": annotation})

    def test_text_over_the_limit_is_refused_as_oversized(self) -> None:
        assert encode_props({"p": "x" * 8}, {}, limit=8) == (("p", "x" * 8),)
        with pytest.raises(BoundarySizeError, match="over 8 characters"):
            encode_props({"p": "x" * 9}, {}, limit=8)

    @pytest.mark.parametrize(
        ("value", "types"),
        [
            pytest.param(2**63, {}, id="unannotated"),
            pytest.param(-(2**63) - 1, {"p": int}, id="int"),
            pytest.param(10**400, {"p": float}, id="float"),
        ],
    )
    def test_a_number_outside_64_bits_is_refused_as_oversized(
        self, value: int, types: dict[str, object]
    ) -> None:
        with pytest.raises(BoundarySizeError, match="the prop 'p' holds a number"):
            encode_props({"p": value}, types)

    @pytest.mark.parametrize(
        ("value", "annotation"),
        [({"a": 1}, str), (1, str), (True, float), ("x", bytes)],
    )
    def test_a_misfit_is_never_taken_for_oversized_or_non_finite(
        self, value: object, annotation: object
    ) -> None:
        with pytest.raises(BoundaryPropsError) as caught:
            encode_props({"p": value}, {"p": annotation})
        assert not isinstance(caught.value, BoundarySizeError | BoundaryValueError)

    @pytest.mark.parametrize(
        ("value", "types", "shown"),
        [
            pytest.param(math.nan, {}, "nan", id="unannotated-nan"),
            pytest.param([1.0, -math.inf], {}, "-inf", id="unannotated-list"),
            pytest.param(math.inf, {"p": float}, "inf", id="float"),
            pytest.param([math.nan], {"p": list[float]}, "nan", id="float-list"),
            pytest.param(Decimal("NaN"), {"p": Decimal}, "NaN", id="decimal-nan"),
            pytest.param(Decimal("sNaN"), {"p": Decimal}, "sNaN", id="decimal-snan"),
            pytest.param(
                Decimal("-Infinity"), {"p": Decimal}, "-Infinity", id="decimal-inf"
            ),
        ],
    )
    def test_a_non_finite_number_is_refused_as_a_value(
        self, value: object, types: dict[str, object], shown: str
    ) -> None:
        with pytest.raises(BoundaryValueError, match=rf"the prop 'p' holds {shown},"):
            encode_props({"p": value}, types)

    def test_the_default_limit_follows_max_bytes(self) -> None:
        assert DEFAULT_LIMIT == 2048

    @pytest.mark.django_db()
    def test_a_model_travels_as_its_primary_key(self) -> None:
        user = User.objects.create(username="ada")
        assert encode_props({"p": user}, {"p": User}) == (("p", user.pk),)

    @pytest.mark.django_db()
    def test_a_text_primary_key_travels_as_text(self) -> None:
        session = Session.objects.create(
            session_key="k" * 32, session_data="", expire_date=timezone.now()
        )
        assert encode_props({"p": session}, {"p": Session}) == (("p", "k" * 32),)

    def test_an_unsaved_model_is_refused(self) -> None:
        with pytest.raises(BoundaryPropsError):
            encode_props({"p": User()}, {"p": User})

    def test_a_primary_key_of_no_wire_type_is_refused(self, dated) -> None:
        with pytest.raises(BoundaryPropsError, match="holds a Ledger"):
            encode_props({"p": dated(day=date(2026, 1, 2))}, {"p": dated})


class TestDecodeProps:
    """``decode_props`` reads each wire value strictly as its declared type."""

    @pytest.mark.parametrize(("annotation", "value", "wire"), ROUND_TRIPS)
    def test_each_type_reads_back_what_was_written(
        self, annotation: object, value: object, wire: object
    ) -> None:
        expected = list(value) if isinstance(value, tuple) else value
        if annotation is float:
            expected = float(value)
        assert _decoded(annotation, wire) == expected

    @pytest.mark.parametrize("value", UNANNOTATED_ROUND_TRIPS)
    def test_an_unannotated_prop_reads_back_with_its_type(self, value: object) -> None:
        wire = dict(encode_props({"p": value}, {}))
        read = decode_props(request_for(), wire, {})["p"]
        assert read == value
        assert type(read) is type(value)

    @pytest.mark.parametrize(
        "wire", [2**63, math.nan, math.inf, [["a"]], {"a": 1}, "x" * 5, ["x" * 5]]
    )
    def test_an_unannotated_wire_value_out_of_shape_is_malformed(
        self, wire: object
    ) -> None:
        with pytest.raises(BoundaryTokenError) as caught:
            decode_props(request_for(), {"p": wire}, {}, limit=4)
        assert caught.value.reason is TokenRejection.MALFORMED

    def test_pairs_and_mappings_both_read(self) -> None:
        request = request_for()
        assert decode_props(request, [("a", "x")], {}) == {"a": "x"}
        assert decode_props(request, {"a": "x"}, {}) == {"a": "x"}

    @pytest.mark.parametrize(
        ("annotation", "wire"),
        [
            (str, 1),
            (str, None),
            (str, ["a"]),
            (int, "1"),
            (int, True),
            (int, 1.0),
            (int, 2**63),
            (float, 1),
            (float, "1.5"),
            (float, True),
            (float, math.nan),
            (float, math.inf),
            (bool, 1),
            (bool, "true"),
            (bool, 0),
            (Decimal, 1.5),
            (Decimal, "1.5e3x"),
            (Decimal, "NaN"),
            (Decimal, "Infinity"),
            (Decimal, "1500.0e0"),
            (Decimal, " 1.5"),
            (date, "2026-1-2"),
            (date, "20260102"),
            (date, "2026-01-02T00:00:00"),
            (date, 20260102),
            (datetime, "2026-01-02 03:04:05"),
            (datetime, "nonsense"),
            (UUID, _UUID.hex),
            (UUID, str(_UUID).upper()),
            (UUID, "{" + str(_UUID) + "}"),
            (UUID, "nope"),
            (None, 0),
            (None, ""),
            (list[int], 1),
            (list[int], ["1"]),
            (list[int], [None]),
            (list[int], None),
            (int | None, "1"),
        ],
    )
    def test_a_wire_value_of_another_type_is_malformed(
        self, annotation: object, wire: object
    ) -> None:
        assert _refusal(annotation, wire) is TokenRejection.MALFORMED

    def test_text_over_the_limit_is_malformed(self) -> None:
        request = request_for()
        assert decode_props(request, {"p": "x" * 4}, {}, limit=4) == {"p": "xxxx"}
        with pytest.raises(BoundaryTokenError):
            decode_props(request, {"p": "x" * 5}, {}, limit=4)

    def test_an_annotation_no_token_carries_is_refused(self) -> None:
        with pytest.raises(BoundaryPropsError, match="which no token carries"):
            _decoded(dict, "x")

    @pytest.mark.django_db()
    def test_a_model_is_fetched_by_its_primary_key(self) -> None:
        user = User.objects.create(username="ada")
        assert _decoded(User, user.pk) == user

    @pytest.mark.django_db()
    def test_a_text_primary_key_is_fetched_as_text(self) -> None:
        session = Session.objects.create(
            session_key="k" * 32, session_data="", expire_date=timezone.now()
        )
        assert _decoded(Session, "k" * 32) == session

    @pytest.mark.django_db()
    def test_a_missing_row_is_not_found_rather_than_a_server_error(self) -> None:
        with pytest.raises(Http404):
            _decoded(User, 404)

    @pytest.mark.parametrize("wire", ["1", 1.0, True, None, ["1"], "x" * 3000])
    def test_a_primary_key_of_another_type_is_malformed(self, wire: object) -> None:
        assert _refusal(User, wire) is TokenRejection.MALFORMED

    def test_a_primary_key_its_field_refuses_is_malformed(self) -> None:
        assert _refusal(User, 2**63) is TokenRejection.MALFORMED

    def test_a_primary_key_of_no_wire_type_is_malformed(self, dated) -> None:
        assert _refusal(dated, "2026-01-02") is TokenRejection.MALFORMED

    def test_a_custom_fetch_receives_the_request_model_and_typed_key(self) -> None:
        request = request_for()
        seen: list[object] = []

        def fetched(got_request, model, pk):
            seen.append((got_request, model, pk))
            return User(pk=pk, username="ada")

        props = decode_props(request, {"p": 9}, {"p": User}, fetch=fetched)
        assert props["p"].username == "ada"
        assert seen == [(request, User, 9)]

    @pytest.mark.django_db()
    def test_the_default_fetch_reads_the_default_manager(self) -> None:
        user = User.objects.create(username="ada")
        assert fetch(request_for(), User, user.pk) == user
