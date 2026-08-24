from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Context, Template

from next.components import (
    ComponentContextManager,
    ComponentInfo,
    components_manager,
    render_component,
)
from next.components.renderers import (
    _RESERVED_CONTEXT_KEYS,
    COMPONENT_PROPS_CONTEXT_KEY,
    _inject_component_context,
)
from tests.support.components import build_composite_component


RESERVED_KEYS = sorted(_RESERVED_CONTEXT_KEYS)

# The inventory as the docs publish it, pinned so a change to the core set has
# to be made twice on purpose.
PINNED_RESERVED_KEYS = frozenset(
    {
        "_component_props",
        "_static_collector",
        "children",
        "csrf_token",
        "current_component_module_path",
        "current_template_path",
        "request",
    }
)


def _inject(
    manager: ComponentContextManager, info: ComponentInfo, context_data: dict
) -> None:
    """Run the injection step with `manager` standing in for the global one."""
    with patch("next.components.renderers.component", manager):
        _inject_component_context(info, context_data, None)


class TestKeylessContextCollisions:
    """A bare `@component.context` may not overwrite props or reserved keys."""

    def test_prop_key_raises(self, tmp_path: Path) -> None:
        """Returning a key that the calling tag passed as a prop raises."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})
        context_data = {
            COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"}),
            "title": "from prop",
        }

        with pytest.raises(
            ValueError, match="Component 'card' context returns 'title'"
        ):
            _inject(mgr, info, context_data)

        assert context_data["title"] == "from prop"

    def test_reserved_inventory_is_pinned(self) -> None:
        """Adding or dropping a reserved key is a decision, not a silent edit."""
        assert _RESERVED_CONTEXT_KEYS == PINNED_RESERVED_KEYS

    @pytest.mark.parametrize("key", RESERVED_KEYS)
    def test_reserved_key_raises_and_keeps_the_render_value(
        self, key: str, tmp_path: Path
    ) -> None:
        """Every reserved key raises and the value the render path put there stands."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {key: "hijacked"})
        original = object()
        context_data = {key: original}

        with pytest.raises(ValueError, match=f"returns {key!r}"):
            _inject(mgr, info, context_data)

        assert context_data[key] is original

    def test_non_string_keys_merge_without_raising(self, tmp_path: Path) -> None:
        """A dict keyed by non-strings survives the slot-prefix scan."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {1: "one", "ok": "yes"})
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})}

        _inject(mgr, info, context_data)

        assert context_data[1] == "one"
        assert context_data["ok"] == "yes"

    def test_slot_prefixed_key_raises(self, tmp_path: Path) -> None:
        """Any `slot_` prefixed key is reserved for slot content."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"slot_footer": "hijacked"})
        context_data = {"slot_footer": "<footer/>"}

        with pytest.raises(ValueError, match="'slot_footer'"):
            _inject(mgr, info, context_data)

        assert context_data["slot_footer"] == "<footer/>"

    def test_message_lists_every_conflict(self, tmp_path: Path) -> None:
        """The message names all conflicting keys in sorted order."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(
            module_path, None, lambda: {"slot_a": 1, "children": 2, "kept": 3}
        )

        with pytest.raises(ValueError, match="returns 'children', 'slot_a',"):
            _inject(mgr, info, {})


class TestKeylessContextMerges:
    """Names outside the guarded domain keep merging silently."""

    def test_non_conflicting_keys_merge(self, tmp_path: Path) -> None:
        """A dict touching nothing guarded lands in the context."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"env": "prod"})
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"})}

        _inject(mgr, info, context_data)

        assert context_data["env"] == "prod"

    def test_inherited_page_key_is_shadowed(self, tmp_path: Path) -> None:
        """A page key that is not a prop of this call may be shadowed."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "component wins"})
        context_data = {
            COMPONENT_PROPS_CONTEXT_KEY: frozenset({"variant"}),
            "title": "page title",
            "variant": "wide",
        }

        _inject(mgr, info, context_data)

        assert context_data["title"] == "component wins"
        assert context_data["variant"] == "wide"

    def test_current_page_module_path_is_shadowed(self, tmp_path: Path) -> None:
        """`current_page_module_path` stays a plain page key, not a reserved one."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(
            module_path, None, lambda: {"current_page_module_path": "/other/page.py"}
        )
        context_data = {"current_page_module_path": "/app/page.py"}

        _inject(mgr, info, context_data)

        assert context_data["current_page_module_path"] == "/other/page.py"

    def test_same_key_merges_when_tag_passes_no_prop(self, tmp_path: Path) -> None:
        """The guard follows the call site, so a propless call still merges."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})
        context_data = {COMPONENT_PROPS_CONTEXT_KEY: frozenset()}

        _inject(mgr, info, context_data)

        assert context_data["title"] == "from context"

    def test_reading_a_prop_and_returning_a_new_key_works(self, tmp_path: Path) -> None:
        """The supported way to build on a prop is a parameter named after it."""
        mgr, info, module_path = build_composite_component(tmp_path)

        def headline(title: str) -> dict:
            return {"headline": title.upper()}

        mgr._registry.register(module_path, None, headline)
        context_data = {
            COMPONENT_PROPS_CONTEXT_KEY: frozenset({"title"}),
            "title": "hello",
        }

        _inject(mgr, info, context_data)

        assert context_data["headline"] == "HELLO"
        assert context_data["title"] == "hello"

    def test_keyed_context_still_overwrites(self, tmp_path: Path) -> None:
        """A keyed registration names one key explicitly and is not guarded."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, "children", lambda: "keyed")
        context_data = {"children": "<i>body</i>"}

        _inject(mgr, info, context_data)

        assert context_data["children"] == "keyed"


class TestRenderComponentWithoutTag:
    """A direct `render_component` carries no prop names, so only keys are reserved."""

    def test_plain_key_merges(self, tmp_path: Path) -> None:
        """Without `_component_props` an ordinary key merges as before."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})

        with patch("next.components.renderers.component", mgr):
            html = render_component(info, {"title": "caller"})

        assert "from context" in html

    def test_reserved_key_still_raises(self, tmp_path: Path) -> None:
        """The reserved names are guarded even without a calling tag."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"children": "x"})

        with (
            patch("next.components.renderers.component", mgr),
            pytest.raises(ValueError, match="'children'"),
        ):
            render_component(info, {})


class TestComponentTagPropsChannel:
    """`{% component %}` publishes its prop names so the guard can see them."""

    def _render(self, mgr: ComponentContextManager, info: ComponentInfo, source: str):
        template = Template("{% load components %}" + source)
        context = Context(
            {"current_template_path": str(info.scope_root / "template.djx")}
        )
        with (
            patch.object(components_manager, "get_component", return_value=info),
            patch("next.components.renderers.component", mgr),
        ):
            return template.render(context)

    def test_prop_collision_raises_through_the_tag(self, tmp_path: Path) -> None:
        """A prop named on the tag is guarded during a real render."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "hijacked"})

        with pytest.raises(
            ValueError, match="Component 'card' context returns 'title'"
        ):
            self._render(mgr, info, '{% component "card" title="Hi" %}')

    def test_no_prop_on_the_tag_merges_through_the_tag(self, tmp_path: Path) -> None:
        """The same component without that prop keeps merging."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"title": "from context"})

        html = self._render(mgr, info, '{% component "card" %}')

        assert "from context" in html

    def test_prop_value_reaches_a_context_parameter(self, tmp_path: Path) -> None:
        """A context function reads the prop by parameter name during a render."""
        mgr, info, module_path = build_composite_component(
            tmp_path, template="<div>{{ headline }}</div>"
        )

        def headline(title: str) -> dict:
            return {"headline": title.upper()}

        mgr._registry.register(module_path, None, headline)

        html = self._render(mgr, info, '{% component "card" title="Hi" %}')

        assert "HI" in html

    def test_slot_content_is_guarded_through_the_tag(self, tmp_path: Path) -> None:
        """Slot HTML gathered by the tag cannot be overwritten by the context."""
        mgr, info, module_path = build_composite_component(tmp_path)
        mgr._registry.register(module_path, None, lambda: {"slot_body": "hijacked"})

        with pytest.raises(ValueError, match="'slot_body'"):
            self._render(
                mgr,
                info,
                '{% #component "card" %}{% #slot "body" %}ok{% /slot %}{% /component %}',
            )
