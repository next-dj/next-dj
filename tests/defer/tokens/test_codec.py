import json
import math

import pytest

from next.defer import BoundaryPropsError, BoundaryTokenError, TokenRejection
from next.defer.tokens import VERSION, BoundaryClaims, codec
from next.defer.tokens.codec import claims_of, envelope
from tests.support.tokens import claims


def _stamped() -> BoundaryClaims:
    held = claims(sku="A1", count=3)
    return BoundaryClaims(
        component=held.component,
        scope=held.scope,
        props=held.props,
        url_path=held.url_path,
        url_kwargs=held.url_kwargs,
        perms=held.perms,
        issued=1_000_000,
        expires=1_000_600,
    )


def _malformed(data: bytes, version: int = VERSION) -> None:
    with pytest.raises(BoundaryTokenError) as caught:
        codec.loads(data, version)
    assert caught.value.reason is TokenRejection.MALFORMED


def _json(**changes: object) -> bytes:
    held = envelope(_stamped())
    held.update(changes)
    return json.dumps(held).encode()


class TestCodec:
    """The claims codec writes compact sorted JSON and reads it back strictly."""

    def test_claims_round_trip(self) -> None:
        assert codec.loads(codec.dumps(_stamped()), VERSION) == _stamped()

    def test_equal_claims_give_equal_compact_sorted_bytes(self) -> None:
        data = codec.dumps(_stamped())
        assert data == codec.dumps(_stamped())
        assert data.startswith(
            b'{"c":"shop/card","e":1000600,"i":1000000,"k":{"id":"5"}'
        )
        assert b" " not in data

    def test_a_prop_outside_the_wire_form_is_refused(self) -> None:
        bad = BoundaryClaims("c", "s", props=(("p", {"a": 1}),))
        with pytest.raises(BoundaryPropsError, match="wire form"):
            codec.dumps(bad)

    def test_an_integer_json_cannot_write_is_refused(self) -> None:
        bad = BoundaryClaims("c", "s", props=(("p", 10**5000),))
        with pytest.raises(BoundaryPropsError, match="JSON cannot carry"):
            codec.dumps(bad)

    @pytest.mark.parametrize(
        "data",
        [
            b"",
            b"\xff\xfe",
            b"[]",
            b"null",
            b"{" * 5000,
            b'{"c":1}',
            b'{"c":"a","c":"b","e":2,"i":1,"k":{},"m":[],"p":{},"s":"s","u":"/","v":2}',
            b'{"c":"a","e":2,"i":1,"k":{},"m":[],"p":{"x":NaN},"s":"s","u":"/","v":2}',
            (
                b'{"c":"a","e":2,"i":1,"k":{},"m":[],"p":{"x":Infinity},"s":"s",'
                b'"u":"/","v":2}'
            ),
            b'{"c":"a","e":2,"i":1,"k":{},"m":[],"p":{},"s":"s","u":"/","v":2,"x":0}',
            b'{"c":"a","i":1,"k":{},"m":[],"p":{},"s":"s","u":"/","v":2}',
            b'{"c":"a","e":2,"i":1,"k":{},"m":[],"p":{"x":' + b"9" * 5000 + b"},"
            b'"s":"s","u":"/","v":2}',
        ],
    )
    def test_bytes_out_of_shape_are_malformed(self, data: bytes) -> None:
        _malformed(data)

    @pytest.mark.parametrize(
        "changes",
        [
            {"v": VERSION + 1},
            {"v": True},
            {"v": "1"},
            {"c": 1},
            {"s": None},
            {"u": ["/"]},
            {"i": -1},
            {"i": 1.5},
            {"i": True},
            {"e": -1},
            {"e": "600"},
            {"e": False},
            {"k": {"id": 5}},
            {"k": []},
            {"m": "shop.view"},
            {"m": [1]},
            {"p": []},
            {"p": {"x": {"a": 1}}},
            {"p": {"x": [[1]]}},
        ],
    )
    def test_a_field_of_another_type_is_malformed(
        self, changes: dict[str, object]
    ) -> None:
        _malformed(_json(**changes))

    def test_a_payload_of_another_version_is_malformed(self) -> None:
        _malformed(codec.dumps(_stamped()), version=VERSION + 1)

    def test_a_payload_of_the_version_before_is_malformed(self) -> None:
        old = {key: value for key, value in envelope(_stamped()).items() if key != "e"}
        _malformed(json.dumps({**old, "v": 1}).encode(), version=1)

    def test_claims_of_rebuilds_sorted_pairs(self) -> None:
        held = json.loads(_json(p={"b": 1, "a": [1.5, None]}))
        assert claims_of(held, VERSION).props == (("a", [1.5, None]), ("b", 1))

    def test_the_sealed_expiry_round_trips(self) -> None:
        held = codec.loads(codec.dumps(_stamped()), VERSION)
        assert (held.issued, held.expires) == (1_000_000, 1_000_600)

    def test_a_float_that_is_not_finite_never_reaches_the_bytes(self) -> None:
        bad = BoundaryClaims("c", "s", props=(("p", math.nan),))
        with pytest.raises(BoundaryPropsError, match="outside the wire form"):
            codec.dumps(bad)
