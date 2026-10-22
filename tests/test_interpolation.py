import pytest
from django.template import TemplateSyntaxError

from next.interpolation import Interpolation, interpolation_of, scoped


class TestInterpolationOf:
    """`interpolation_of` compiles a plain string outside any template."""

    def test_a_string_without_braces_is_not_an_interpolation(self) -> None:
        assert interpolation_of("plain") is None

    @pytest.mark.parametrize(
        ("text", "scope", "expected"),
        [
            ("Rows ({{ n }})", {"n": 3}, "Rows (3)"),
            ("{{ a }}-{{ b }}", {"a": 1, "b": 2}, "1-2"),
            ("{{ name|upper }}!", {"name": "ada"}, "ADA!"),
            ("{{ n }}", {"n": 5}, 5),
            ("{{ items }}", {"items": [1, 2]}, [1, 2]),
            ("x {{ gone }} y", {}, "x  y"),
            ("a {{ raw }}", {"raw": "<b>"}, "a <b>"),
        ],
    )
    def test_resolves_each_expression(self, text: str, scope: dict, expected) -> None:
        interpolation = interpolation_of(text)
        assert isinstance(interpolation, Interpolation)
        assert interpolation.resolve(scoped(scope)) == expected

    def test_an_empty_expression_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="Empty"):
            interpolation_of("a {{   }} b")

    def test_the_result_is_memoised(self) -> None:
        assert interpolation_of("{{ n }}") is interpolation_of("{{ n }}")
