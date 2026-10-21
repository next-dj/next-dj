import pytest
from django.core.exceptions import ImproperlyConfigured

from next.deps import UnboundObjectError


class TestUnboundObjectError:
    """The error names the declaration that would supply the missing source."""

    @pytest.mark.parametrize(
        ("available", "captured"),
        [(("slug", "id"), "(captured: id, slug)"), ((), "(captured: none)")],
        ids=["captured", "none"],
    )
    def test_a_missing_kwarg_lists_what_the_route_captured(
        self, available: tuple[str, ...], captured: str
    ) -> None:
        error = UnboundObjectError("post", "post", available=available)
        assert str(error) == (
            f'DObject parameter "post" finds no URL kwarg "post" {captured}. It '
            "reads the kwarg named like itself, so rename it after a captured segment."
        )
        assert (error.param_name, error.key, error.parent) == ("post", "post", False)

    def test_a_missing_parent_points_at_an_inherited_context(self) -> None:
        error = UnboundObjectError("comment", "post", parent=True, available=("id",))
        assert str(error) == (
            'DObject parameter "comment" finds no "post" in the context it resolves '
            'in. Declare @context("post", inherit_context=True) in an ancestor '
            "page.py, since a form handler or hook resolves with no page context."
        )
        assert (error.param_name, error.key, error.parent) == ("comment", "post", True)

    def test_it_is_a_configuration_error(self) -> None:
        assert isinstance(UnboundObjectError("post", "post"), ImproperlyConfigured)
