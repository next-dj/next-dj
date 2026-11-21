import math

import pytest

from next.suspense.tokens import VERSION, VERSIONS, BoundaryClaims, url_pairs
from next.suspense.tokens.claims import is_scalar, is_wire


class TestBoundaryClaims:
    """``BoundaryClaims.of`` writes equal claims in one canonical order."""

    def test_props_kwargs_and_perms_are_sorted(self) -> None:
        claims = BoundaryClaims.of(
            "card",
            "pages/shop",
            props=[("sku", "A"), ("count", 2)],
            url_path="/shop/",
            url_kwargs={"slug": "x", "id": 5},
            perms=["b.view", "a.view", "b.view"],
        )
        assert claims.props == (("count", 2), ("sku", "A"))
        assert claims.url_kwargs == (("id", "5"), ("slug", "x"))
        assert claims.perms == ("a.view", "b.view")
        assert (claims.issued, claims.expires, claims.version) == (0, 0, VERSION)

    def test_defaults_name_the_root_path_and_nothing_else(self) -> None:
        claims = BoundaryClaims.of("card", "scope")
        assert (claims.props, claims.url_path, claims.url_kwargs, claims.perms) == (
            (),
            "/",
            (),
            (),
        )

    def test_the_claims_carry_no_access_rule(self) -> None:
        assert set(BoundaryClaims.__slots__) == {
            "component",
            "scope",
            "props",
            "url_path",
            "url_kwargs",
            "perms",
            "issued",
            "expires",
            "version",
        }

    def test_this_code_reads_the_version_it_mints(self) -> None:
        assert VERSIONS[0] == VERSION

    def test_the_sealed_expiry_bumped_the_version_and_only_it_is_read(self) -> None:
        assert (VERSION, VERSIONS) == (2, (2,))


class TestWire:
    """The wire form holds scalars and flat lists of them, never a mapping."""

    @pytest.mark.parametrize("value", ["a", 1, 1.5, True, None])
    def test_a_scalar_is_wire(self, value: object) -> None:
        assert is_scalar(value)
        assert is_wire(value)
        assert is_wire([value, value])

    @pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
    def test_a_float_that_is_not_finite_is_refused(self, value: float) -> None:
        assert not is_scalar(value)
        assert not is_wire([value])

    @pytest.mark.parametrize("value", [{}, {"a": 1}, [[1]], [{"a": 1}], (1,), b"x"])
    def test_a_nested_or_foreign_value_is_refused(self, value: object) -> None:
        assert not is_wire(value)

    def test_url_pairs_write_every_value_as_text(self) -> None:
        assert url_pairs({"b": 2, "a": "x"}) == (("a", "x"), ("b", "2"))
