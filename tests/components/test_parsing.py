import pytest
from django.template import Template, TemplateSyntaxError

from next.components.registry import DeferredComponents
from next.templatetags.components import ComponentNode


class TestComponentArguments:
    """A component tag needs a quoted name before its props."""

    def test_component_tag_requires_name(self) -> None:
        """{% component %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="component name"):
            Template("{% load components %}{% component %}")

    def test_component_tag_requires_quoted_name(self) -> None:
        """{% component %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted"):
            Template('{% load components %}{% component "" %}')

    def test_block_component_requires_name_token(self) -> None:
        """{% #component %} without a name raises."""
        with pytest.raises(TemplateSyntaxError, match="#component"):
            Template("{% load components %}{% #component %}")

    def test_block_component_requires_non_empty_quoted_name(self) -> None:
        """{% #component %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted"):
            Template('{% load components %}{% #component "" %}{% /component %}')


class TestSlotArguments:
    """A slot tag takes exactly one quoted slot name."""

    @pytest.mark.parametrize(
        ("slot", "match"),
        [
            pytest.param("{% #slot %}{% /slot %}", "#slot", id="block-without-name"),
            pytest.param(
                '{% #slot "a" "b" %}{% /slot %}', "exactly one", id="block-two-names"
            ),
            pytest.param('{% slot "" %}', "quoted slot name", id="short-empty-name"),
            pytest.param('{% slot "a" "b" %}', "exactly one", id="short-two-names"),
        ],
    )
    def test_a_slot_without_one_quoted_name_raises(self, slot: str, match: str) -> None:
        with pytest.raises(TemplateSyntaxError, match=match):
            Template(
                '{% load components %}{% #component "c" %}' + slot + "{% /component %}"
            )


class TestSetSlotArguments:
    """A set_slot tag takes exactly one quoted slot name."""

    def test_set_slot_requires_name(self) -> None:
        """{% #set_slot %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="#set_slot"):
            Template("{% load components %}{% #set_slot %}fallback{% /set_slot %}")

    def test_set_slot_requires_quoted_name(self) -> None:
        """{% #set_slot %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template('{% load components %}{% #set_slot "" %}x{% /set_slot %}')

    def test_short_set_slot_requires_exactly_one_name(self) -> None:
        """{% set_slot %} short form with two names raises."""
        with pytest.raises(TemplateSyntaxError, match="exactly one"):
            Template('{% load components %}{% set_slot "a" "b" %}')

    def test_short_set_slot_empty_name_raises(self) -> None:
        """{% set_slot "" %} raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template('{% load components %}{% set_slot "" %}')


class TestBoundaryOptions:
    """The boundary options of a call are literals, and reload= and delay= stay out."""

    @pytest.mark.parametrize(
        "source",
        [
            "{% component 'c' defer='stream' reload='2s' %}",
            "{% component 'c' delay='150ms' %}",
            "{% component 'c' delay=wait %}",
            "{% #component 'c' reload=None %}{% /component %}",
        ],
    )
    def test_reload_and_delay_are_zone_options_only(self, source: str) -> None:
        with pytest.raises(TemplateSyntaxError, match=r"reserves \w+=, an option of"):
            Template(source)

    def test_a_call_site_mode_marks_the_project_deferring(
        self, fresh_deferred_components: DeferredComponents
    ) -> None:
        Template("{% component 'c' defer='stream' %}")
        assert fresh_deferred_components.held is True

    @pytest.mark.parametrize(
        ("source", "message"),
        [
            ("{% component 'c' defer='nope' %}", "refuses its boundary options"),
            ("{% component 'c' defer=mode %}", "takes defer= as a literal"),
            ("{% component 'c' timeout=wait|default:1 %}", "takes timeout= as a lit"),
            ("{% component 'c' timeout='2' %}", "duration literal"),
            ("{% #component 'c' timeout='{{ x }}' %}{% /component %}", "a literal"),
        ],
    )
    def test_a_misfit_option_is_a_compile_error(
        self, source: str, message: str
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match=message):
            Template(source)


class TestConstantProps:
    """A call exposes the props it passes as constants, for a check to read."""

    @pytest.mark.parametrize(
        ("source", "constants"),
        [
            pytest.param(
                "{% component 'c' a=1 b='x' %}", {"a": 1, "b": "x"}, id="literals"
            ),
            pytest.param(
                "{% component 'c' a=True b=False c=None %}",
                {"a": True, "b": False, "c": None},
                id="context-constants",
            ),
            pytest.param("{% component 'c' a=name %}", {}, id="variable"),
            pytest.param("{% component 'c' a=1|add:2 %}", {}, id="filtered"),
            pytest.param("{% component 'c' a='{{ name }}' %}", {}, id="interpolated"),
            pytest.param(
                "{% component 'c' a=1 b=name %}", {"a": 1}, id="literal-beside-variable"
            ),
        ],
    )
    def test_only_what_no_context_changes_is_a_constant(
        self, source: str, constants: dict[str, object]
    ) -> None:
        (node,) = Template(source).nodelist.get_nodes_by_type(ComponentNode)
        assert node.constant_props() == constants
