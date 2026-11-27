from django.template import Template

from next.components.reads import reads


def _reads(source: str) -> tuple[frozenset[str], frozenset[str]]:
    return reads(Template(source).nodelist)


class TestReads:
    """The scan finds the top-level variables a template reads unbound."""

    def test_variables_and_filter_arguments_are_read(self) -> None:
        read, _assigned = _reads("{{ a.b }}{{ c|default:d }}{{ 'x'|default:e.f }}")
        assert read == {"a", "c", "d", "e"}

    def test_conditions_are_read(self) -> None:
        read, _assigned = _reads("{% if a and not b or c == 1 %}{{ d }}{% endif %}")
        assert read == {"a", "b", "c", "d"}

    def test_loop_variables_are_bound_inside_the_loop(self) -> None:
        source = (
            "{% for x, y in rows %}{{ x }}{{ y }}{{ forloop.counter }}{{ z }}"
            "{% endfor %}{{ x }}"
        )
        read, _assigned = _reads(source)
        assert read == {"rows", "z", "x"}

    def test_a_single_loop_variable_is_bound(self) -> None:
        read, _assigned = _reads("{% for x in rows %}{{ x }}{% endfor %}")
        assert read == {"rows"}

    def test_with_names_are_bound_inside_the_block(self) -> None:
        read, _assigned = _reads("{% with a=b %}{{ a }}{% endwith %}{{ a }}")
        assert read == {"b", "a"}

    def test_names_a_tag_assigns_are_reported(self) -> None:
        read, assigned = _reads("{% firstof a b as picked %}{{ picked }}")
        assert {"a", "b", "picked"} <= read
        assert assigned == {"picked"}

    def test_component_props_and_interpolations_are_read(self) -> None:
        read, _assigned = _reads(
            '{% component "card" sku=item.sku label="n {{ count }}" %}'
        )
        assert read == {"item", "count"}
