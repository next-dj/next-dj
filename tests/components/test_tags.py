from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.template.base import TemplateSyntaxError
from django.utils.safestring import SafeString

from next.components import ComponentInfo, components_manager
from next.static import StaticCollector


class TestComponentTag:
    """Tests for void ``{% component %}`` and block ``{% #component %}`` tags."""

    def test_component_tag_requires_name(self) -> None:
        """{% component %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="component name"):
            Template("{% load components %}{% component %}")

    def test_component_tag_requires_quoted_name(self) -> None:
        """{% component %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted"):
            Template('{% load components %}{% component "" %}')

    def test_legacy_endcomponent_is_invalid(self) -> None:
        """{% endcomponent %} is not a registered tag."""
        with pytest.raises(TemplateSyntaxError, match="endcomponent"):
            Template("{% load components %}{% endcomponent %}")

    def test_block_component_requires_name_token(self) -> None:
        """{% #component %} without a name raises."""
        with pytest.raises(TemplateSyntaxError, match="#component"):
            Template("{% load components %}{% #component %}")

    def test_block_component_requires_non_empty_quoted_name(self) -> None:
        """{% #component %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted"):
            Template('{% load components %}{% #component "" %}{% /component %}')

    def test_component_tag_renders_empty_without_path_in_context(self) -> None:
        """When current_template_path is missing, component renders empty."""
        t = Template('{% load components %}{% component "card" title="Hi" %}')
        result = t.render(Context({}))
        assert result == ""

    def test_component_tag_renders_empty_when_path_not_str_or_path(self) -> None:
        """When current_template_path is not str/Path (e.g. int), component renders empty."""
        t = Template('{% load components %}{% component "card" %}')
        result = t.render(Context({"current_template_path": 42}))
        assert result == ""

    def test_component_tag_renders_empty_when_component_not_found(self) -> None:
        """When component is not resolved, renders empty."""
        t = Template('{% load components %}{% component "nonexistent" %}')
        with patch("next.templatetags.components.get_component", return_value=None):
            result = t.render(
                Context({"current_template_path": "/app/pages/home/template.djx"}),
            )
        assert result == ""

    def test_component_tag_renders_component_when_found(self, tmp_path: Path) -> None:
        """When component is found and path in context, renders template."""
        (tmp_path / "card.djx").write_text('<div class="card">{{ title }}</div>')
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="card",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "card.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template('{% load components %}{% component "card" title="Hello" %}')
            result = t.render(
                Context({"current_template_path": str(tmp_path / "template.djx")}),
            )
        assert 'class="card"' in result
        assert "Hello" in result

    def test_component_tag_inherits_parent_template_context(
        self, tmp_path: Path
    ) -> None:
        """Parent page context keys are visible inside the component template."""
        (tmp_path / "banner.djx").write_text(
            '<span class="banner">{{ page_var }}</span>'
        )
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="banner",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "banner.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template('{% load components %}{% component "banner" %}')
            result = t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "template.djx"),
                        "page_var": "from_parent_page",
                    },
                ),
            )
        assert "from_parent_page" in result

    def test_component_tag_prop_overrides_parent_context_key(
        self, tmp_path: Path
    ) -> None:
        """Explicit tag props shadow parent context keys of the same name."""
        (tmp_path / "chip.djx").write_text("<em>{{ title }}</em>")
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="chip",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "chip.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                '{% load components %}{% component "chip" title="from_prop" %}'
            )
            result = t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "t.djx"),
                        "title": "from_parent",
                    },
                ),
            )
        assert "from_prop" in result
        assert "from_parent" not in result

    def test_component_tag_accepts_path_object_in_context(self, tmp_path: Path) -> None:
        """current_template_path in context can be a Path object."""
        with patch.object(
            components_manager,
            "get_component",
            return_value=None,
        ):
            t = Template('{% load components %}{% component "c" %}')
            t.render(Context({"current_template_path": tmp_path / "t.djx"}))

    def test_component_tag_triggers_static_discovery_for_composite(
        self, tmp_path: Path
    ) -> None:
        """A composite component render triggers default_manager discovery."""
        (tmp_path / "card.djx").write_text('<div class="card">ok</div>')
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=False,
        )
        collector = StaticCollector()
        with (
            patch.object(components_manager, "get_component", return_value=info),
            patch(
                "next.templatetags.components.default_manager.discover_component_assets"
            ) as spy,
        ):
            t = Template('{% load components %}{% component "card" %}')
            t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "template.djx"),
                        "_static_collector": collector,
                    },
                ),
            )
        spy.assert_called_once()
        (called_info, called_collector) = spy.call_args.args
        assert called_info is info
        assert called_collector is collector

    @pytest.mark.parametrize(
        ("call_site", "expected_substring", "forbidden_substrings"),
        [
            # Void form passes ``description`` only as a prop. The slot
            # was never injected so the default body must render.
            (
                '{% component "card" description="prop value" %}',
                "<i>fallback</i>",
                ("prop value",),
            ),
            # Block form passes ``description`` both as a prop and as a
            # slot body. The injected slot wins over the prop and over
            # the default body.
            (
                '{% #component "card" description="prop value" %}'
                '{% #slot "description" %}<b>injected</b>{% /slot %}'
                "{% /component %}",
                "<b>injected</b>",
                ("fallback", "prop value"),
            ),
        ],
        ids=["prop-does-not-shadow-default", "slot-wins-over-same-named-prop"],
    )
    def test_block_component_slot_namespace_isolated_from_props(
        self,
        tmp_path: Path,
        call_site: str,
        expected_substring: str,
        forbidden_substrings: tuple[str, ...],
    ) -> None:
        """End-to-end: props named like a slot never leak into the slot lookup.

        The component template wraps its content in
        ``{% #set_slot "description" %}<i>fallback</i>{% /set_slot %}``.
        Earlier versions of the renderer mirrored slot content under the
        unprefixed ``<name>`` key, so passing a ``description`` prop
        caused the default body to be replaced by the prop value.
        """
        (tmp_path / "card.djx").write_text(
            "<article>"
            '{% #set_slot "description" %}<i>fallback</i>{% /set_slot %}'
            "</article>",
        )
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template("{% load components %}" + call_site)
            result = t.render(
                Context({"current_template_path": str(tmp_path / "page.djx")}),
            )
        assert expected_substring in result
        for forbidden in forbidden_substrings:
            assert forbidden not in result

    @pytest.mark.parametrize(
        ("call_site", "context_extra", "must_contain", "must_not_contain"),
        [
            # Plain string literal: Django's FilterExpression marks bare
            # quoted literals as safe by convention, so without the
            # component-side demotion the ``<slug>`` token would be
            # parsed by the browser as a tag and disappear visually.
            (
                '{% component "card" body="visit /s/<slug>/ now" %}',
                {},
                ("/s/&lt;slug&gt;/",),
                ("<slug>",),
            ),
            # Explicit ``|safe`` on the literal opts back in to raw HTML
            # so callers can still inject pre-built markup when needed.
            (
                '{% component "card" body="<em>raw</em>"|safe %}',
                {},
                ("<em>raw</em>",),
                ("&lt;em&gt;",),
            ),
            # A variable whose value is already a ``SafeString`` keeps
            # its safe marker through the resolver.
            (
                '{% component "card" body=html_blob %}',
                {"html_blob": "<b>safe</b>"},
                ("<b>safe</b>",),
                ("&lt;b&gt;",),
            ),
        ],
        ids=["literal-escapes", "literal-safe-opt-in", "variable-stays-safe"],
    )
    def test_component_props_demote_safe_literals_for_text_display(
        self,
        tmp_path: Path,
        call_site: str,
        context_extra: dict[str, str],
        must_contain: tuple[str, ...],
        must_not_contain: tuple[str, ...],
    ) -> None:
        """Plain string literals reach ``{{ prop }}`` as text, not as HTML.

        Earlier the renderer relied on ``FilterExpression.resolve`` which
        auto-marks bare quoted literals as safe, so something like
        ``description="visit /s/<slug>/"`` ended up interpolated as raw
        HTML and the ``<slug>`` token was eaten by the browser parser.
        ``ComponentNode._resolved_props`` now strips the safe marker from
        literals without a filter chain, so component props behave like
        user-facing text by default while ``|safe`` (or a safe variable)
        stays available as an explicit opt-in.
        """
        (tmp_path / "card.djx").write_text("<article>{{ body }}</article>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        ctx = {"current_template_path": str(tmp_path / "page.djx")}
        for key, value in context_extra.items():
            ctx[key] = SafeString(value)
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template("{% load components %}" + call_site)
            result = t.render(Context(ctx))
        for needle in must_contain:
            assert needle in result
        for forbidden in must_not_contain:
            assert forbidden not in result

    def test_block_component_with_slots_passes_slot_content(
        self, tmp_path: Path
    ) -> None:
        """When #component body has #slot, content is passed to component."""
        (tmp_path / "box.djx").write_text(
            '<div class="box">{{ slot_image }} {{ children }}</div>',
        )
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="box",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "box.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                "{% load components %}"
                '{% #component "box" %}'
                '{% #slot "image" %}<img src="x"/>{% /slot %}'
                "kids"
                "{% /component %}"
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "template.djx")}),
            )
        assert "slot_image" in result or "<img" in result
        assert "kids" in result

    def test_component_tag_with_props(self, tmp_path: Path) -> None:
        """Component tag parses key=val props."""
        (tmp_path / "card.djx").write_text("<h1>{{ title }}</h1>")
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="card",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "card.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template('{% load components %}{% component "card" title="My Title" %}')
            result = t.render(
                Context({"current_template_path": str(tmp_path / "t.djx")}),
            )
        assert "My Title" in result

    def test_component_tag_ignores_token_without_equals(self, tmp_path: Path) -> None:
        """Extra words without ``=`` in the opening tag are skipped for props."""
        (tmp_path / "card.djx").write_text("<h1>{{ title }}</h1>")
        with patch.object(
            components_manager,
            "get_component",
            return_value=ComponentInfo(
                name="card",
                scope_root=tmp_path,
                scope_relative="",
                template_path=tmp_path / "card.djx",
                module_path=None,
                is_simple=True,
            ),
        ):
            t = Template(
                '{% load components %}{% component "card" orphan title="Kept" %}'
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "t.djx")}),
            )
        assert "Kept" in result

    def test_component_tag_resolves_variable_prop_from_context(
        self, tmp_path: Path
    ) -> None:
        """A bare identifier resolves as a context variable, not a literal."""
        (tmp_path / "card.djx").write_text("<h1>{{ title }}</h1>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template('{% load components %}{% component "card" title=page_title %}')
            result = t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "t.djx"),
                        "page_title": "From Context",
                    }
                ),
            )
        assert "From Context" in result
        assert "page_title" not in result

    def test_component_tag_resolves_dotted_attribute_lookup(
        self, tmp_path: Path
    ) -> None:
        """Dotted props traverse attributes, dict keys, and sequence indices."""
        (tmp_path / "card.djx").write_text("<h1>{{ title }}</h1>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template('{% load components %}{% component "card" title=user.name %}')
            result = t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "t.djx"),
                        "user": {"name": "Ada"},
                    }
                ),
            )
        assert "Ada" in result

    def test_component_tag_resolves_numeric_literal(self, tmp_path: Path) -> None:
        """Unquoted numbers resolve to ints, not strings."""
        (tmp_path / "card.djx").write_text("<span>{{ count|add:0 }}</span>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template('{% load components %}{% component "card" count=42 %}')
            result = t.render(
                Context({"current_template_path": str(tmp_path / "t.djx")}),
            )
        assert "42" in result

    def test_component_tag_passes_object_identity_through(self, tmp_path: Path) -> None:
        """A prop bound to an object resolves to that same instance for the child."""
        (tmp_path / "card.djx").write_text("{% if data.flag %}YES{% endif %}")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        marker = {"flag": True, "id": object()}
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template('{% load components %}{% component "card" data=payload %}')
            result = t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "t.djx"),
                        "payload": marker,
                    }
                ),
            )
        assert "YES" in result

    def test_component_tag_applies_filter_chain(self, tmp_path: Path) -> None:
        """Prop values can carry Django filters that run at render time."""
        (tmp_path / "card.djx").write_text("<em>{{ title }}</em>")
        info = ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "card.djx",
            module_path=None,
            is_simple=True,
        )
        with patch.object(components_manager, "get_component", return_value=info):
            t = Template('{% load components %}{% component "card" title=name|upper %}')
            result = t.render(
                Context(
                    {
                        "current_template_path": str(tmp_path / "t.djx"),
                        "name": "ada",
                    }
                ),
            )
        assert "ADA" in result

    def test_nested_components_three_levels(self, tmp_path: Path) -> None:
        """{% #component %} inside component.djx resolves nested names."""
        (tmp_path / "inner.djx").write_text("<i>inner</i>")
        (tmp_path / "mid.djx").write_text(
            '{% #component "inner" %}{% /component %}',
        )
        (tmp_path / "outer.djx").write_text(
            '{% #component "mid" %}{% /component %}',
        )

        def fake_get(name: str, _: Path) -> ComponentInfo | None:
            mapping = {
                "outer": tmp_path / "outer.djx",
                "mid": tmp_path / "mid.djx",
                "inner": tmp_path / "inner.djx",
            }
            p = mapping.get(name)
            if p is None:
                return None
            return ComponentInfo(
                name=name,
                scope_root=tmp_path,
                scope_relative="",
                template_path=p,
                module_path=None,
                is_simple=True,
            )

        with patch.object(components_manager, "get_component", side_effect=fake_get):
            t = Template(
                "{% load components %}{% #component 'outer' %}{% /component %}",
            )
            result = t.render(
                Context({"current_template_path": str(tmp_path / "page.djx")}),
            )
        assert "<i>inner</i>" in result

    def test_orphan_slash_component_raises(self) -> None:
        """{% /component %} without opening #component raises."""
        with pytest.raises(TemplateSyntaxError, match="/component"):
            Template("{% load components %}{% /component %}")


class TestSlotTag:
    """Tests for ``{% #slot %}``, ``{% /slot %}``, and short ``{% slot %}``."""

    def test_block_slot_tag_requires_name(self) -> None:
        """{% #slot %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="#slot"):
            Template(
                "{% load components %}"
                '{% #component "c" %}{% #slot %}{% /slot %}{% /component %}'
            )

    def test_block_slot_tag_requires_exactly_one_arg(self) -> None:
        """{% #slot %} with wrong arity raises."""
        with pytest.raises(TemplateSyntaxError, match="exactly one"):
            Template(
                "{% load components %}"
                '{% #component "c" %}{% #slot "a" "b" %}{% /slot %}{% /component %}'
            )

    def test_short_slot_requires_quoted_name(self) -> None:
        """{% slot %} short form with empty name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template(
                '{% load components %}{% #component "c" %}{% slot "" %}{% /component %}'
            )

    def test_short_slot_requires_exactly_one_name(self) -> None:
        """{% slot %} short form with two names raises."""
        with pytest.raises(TemplateSyntaxError, match="exactly one"):
            Template(
                "{% load components %}"
                '{% #component "c" %}{% slot "a" "b" %}{% /component %}'
            )

    def test_block_slot_parses_inside_block_component(self) -> None:
        """{% #slot %} … {% /slot %} parses inside {% #component %}."""
        t = Template(
            "{% load components %}"
            '{% #component "c" %}'
            '{% #slot "image" %}<img/>{% /slot %}'
            "{% /component %}"
        )
        t.render(Context({"current_template_path": "/x"}))

    def test_short_slot_parses_inside_block_component(self) -> None:
        """{% slot "name" %} short form compiles inside {% #component %}."""
        t = Template(
            "{% load components %}"
            '{% #component "c" %}{% slot "footer" %}{% /component %}'
        )
        t.render(Context({"current_template_path": "/x"}))

    def test_orphan_slash_slot_raises(self) -> None:
        """{% /slot %} without opening #slot raises."""
        with pytest.raises(TemplateSyntaxError, match="/slot"):
            Template("{% load components %}{% /slot %}")


class TestSetSlotTag:
    """Tests for ``{% #set_slot %}``, ``{% /set_slot %}``, and short ``{% set_slot %}``."""

    def test_set_slot_requires_name(self) -> None:
        """{% #set_slot %} without name raises TemplateSyntaxError."""
        with pytest.raises(TemplateSyntaxError, match="#set_slot"):
            Template("{% load components %}{% #set_slot %}fallback{% /set_slot %}")

    def test_set_slot_requires_quoted_name(self) -> None:
        """{% #set_slot %} with empty quoted name raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template('{% load components %}{% #set_slot "" %}x{% /set_slot %}')

    def test_set_slot_renders_fallback_when_slot_empty(self) -> None:
        r"""{% #set_slot %} renders fallback when slot not in context."""
        t = Template(
            "{% load components %}"
            '{% #set_slot "avatar" %}<span>default</span>{% /set_slot %}'
        )
        result = t.render(Context({}))
        assert "<span>default</span>" in result

    def test_set_slot_renders_slot_content_when_in_context(self) -> None:
        """{% #set_slot %} renders slot_xxx from context when present."""
        t = Template(
            "{% load components %}"
            '{% #set_slot "avatar" %}<span>default</span>{% /set_slot %}'
        )
        result = t.render(Context({"slot_avatar": '<img src="x"/>'}))
        assert '<img src="x"/>' in result

    def test_orphan_slash_set_slot_raises(self) -> None:
        """{% /set_slot %} without opening #set_slot raises."""
        with pytest.raises(TemplateSyntaxError, match="/set_slot"):
            Template("{% load components %}{% /set_slot %}")

    def test_short_set_slot_renders_empty_when_slot_missing(self) -> None:
        """{% set_slot "x" %} void form has no default, renders empty when slot absent."""
        t = Template('{% load components %}{% set_slot "label" %}')
        assert t.render(Context({})) == ""

    def test_short_set_slot_renders_slot_from_context(self) -> None:
        """Short {% set_slot %} prefers injected slot HTML from context."""
        t = Template('{% load components %}{% set_slot "avatar" %}')
        result = t.render(Context({"slot_avatar": "<b>ok</b>"}))
        assert "<b>ok</b>" in result

    def test_short_set_slot_requires_exactly_one_name(self) -> None:
        """{% set_slot %} short form with two names raises."""
        with pytest.raises(TemplateSyntaxError, match="exactly one"):
            Template(
                '{% load components %}{% set_slot "a" "b" %}',
            )

    def test_short_set_slot_empty_name_raises(self) -> None:
        """{% set_slot "" %} raises."""
        with pytest.raises(TemplateSyntaxError, match="quoted slot name"):
            Template('{% load components %}{% set_slot "" %}')

    @pytest.mark.parametrize(
        ("context_data", "expected_substring", "forbidden_substrings"),
        [
            # No slot key in context: prop with the same name as the slot
            # must not shadow the default body. Earlier versions fell back
            # to the unprefixed ``<name>`` key, which leaked props into
            # the slot lookup.
            (
                {"description": "prop value"},
                "<span>default</span>",
                ("prop value",),
            ),
            # Both slot_<name> and a same-named prop are present: the
            # caller-injected slot wins over both the prop and the
            # default fallback body.
            (
                {"slot_description": "<em>real</em>", "description": "prop"},
                "<em>real</em>",
                ("prop", "default"),
            ),
        ],
        ids=["prop-does-not-shadow-default", "slot-wins-over-same-named-prop"],
    )
    def test_set_slot_isolation_from_props(
        self,
        context_data: dict[str, str],
        expected_substring: str,
        forbidden_substrings: tuple[str, ...],
    ) -> None:
        """Slot lookup honours ``slot_<name>`` only and never falls back to props."""
        t = Template(
            "{% load components %}"
            '{% #set_slot "description" %}<span>default</span>{% /set_slot %}',
        )
        result = t.render(Context(context_data))
        assert expected_substring in result
        for forbidden in forbidden_substrings:
            assert forbidden not in result

    def test_set_slot_renders_empty_when_slot_injected_empty(self) -> None:
        """An explicitly empty slot renders nothing and skips the fallback body.

        Passing ``{% #slot "x" %}{% /slot %}`` at the call site sets
        ``slot_x`` to an empty string. The default body must not run in
        that case — the caller asked for the slot to render empty.
        """
        t = Template(
            '{% load components %}{% #set_slot "label" %}fallback{% /set_slot %}',
        )
        result = t.render(Context({"slot_label": ""}))
        assert result == ""
