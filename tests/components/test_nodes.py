from django.template import Template

from next.components.nodes import ComponentTagNode, SetSlotTagNode, SlotTagNode


class TestBaseClasses:
    """The compiled component tags are found through their framework base classes."""

    def test_a_component_body_holds_its_slots(self) -> None:
        template = Template(
            '{% #component "card" %}{% #slot "title" %}Hi{% /slot %}'
            "body{% /component %}"
        )
        (component,) = template.nodelist.get_nodes_by_type(ComponentTagNode)
        (slot,) = template.nodelist.get_nodes_by_type(SlotTagNode)
        assert component.name == "card"
        assert slot.name == "title"
        assert slot.owner is component
        assert slot in component.nodelist

    def test_a_nested_component_owns_its_own_slot(self) -> None:
        template = Template(
            '{% #component "outer" %}{% #component "inner" %}'
            '{% #slot "aside" %}x{% /slot %}{% /component %}{% /component %}'
        )
        outer, inner = template.nodelist.get_nodes_by_type(ComponentTagNode)
        (slot,) = template.nodelist.get_nodes_by_type(SlotTagNode)
        assert (outer.name, inner.name) == ("outer", "inner")
        assert slot.owner is inner

    def test_a_slot_outside_a_component_has_no_owner(self) -> None:
        template = Template('{% #slot "title" %}Hi{% /slot %}')
        (slot,) = template.nodelist.get_nodes_by_type(SlotTagNode)
        assert slot.owner is None

    def test_set_slot_names_the_slot_and_keeps_its_fallback(self) -> None:
        template = Template('<h1>{% #set_slot "title" %}Untitled{% /set_slot %}</h1>')
        (node,) = template.nodelist.get_nodes_by_type(SetSlotTagNode)
        assert node.name == "title"
        assert [child.s for child in node.nodelist] == ["Untitled"]

    def test_the_short_set_slot_has_an_empty_fallback(self) -> None:
        template = Template('<h1>{% set_slot "title" %}</h1>')
        (node,) = template.nodelist.get_nodes_by_type(SetSlotTagNode)
        assert node.name == "title"
        assert list(node.nodelist) == []
