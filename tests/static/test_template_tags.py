from __future__ import annotations

from django.template import Context, Template

from next.static import StaticCollector


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


def _render(
    source: str, context_data: dict | None = None
) -> tuple[str, StaticCollector]:
    collector = StaticCollector()
    template = Template(source)
    ctx = Context(context_data or {})
    ctx["_static_collector"] = collector
    return template.render(ctx), collector


class TestCollectPlaceholderTags:
    def test_collect_styles_emits_placeholder(self) -> None:
        out, _ = _render("{% load next_static %}{% collect_styles %}")
        assert out == STYLES_PLACEHOLDER

    def test_collect_scripts_emits_placeholder(self) -> None:
        out, _ = _render("{% load next_static %}{% collect_scripts %}")
        assert out == SCRIPTS_PLACEHOLDER


class TestUseStyleScriptInlineTags:
    def test_use_style_registers_and_emits_nothing(self) -> None:
        out, coll = _render('{% load next_static %}{% use_style "https://cdn/a.css" %}')
        assert out == ""
        assert [a.url for a in coll.assets_in_slot("styles")] == ["https://cdn/a.css"]

    def test_use_script_registers_and_emits_nothing(self) -> None:
        out, coll = _render('{% load next_static %}{% use_script "https://cdn/a.js" %}')
        assert out == ""
        assert [a.url for a in coll.assets_in_slot("scripts")] == ["https://cdn/a.js"]

    def test_empty_url_is_ignored(self) -> None:
        out, coll = _render('{% load next_static %}{% use_style "" %}')
        assert out == ""
        assert coll.assets_in_slot("styles") == []

    def test_no_collector_in_context_is_no_op(self) -> None:
        template = Template('{% load next_static %}{% use_style "https://cdn/a.css" %}')
        result = template.render(Context({}))
        assert result == ""


class TestBlockUseStyleScript:
    def test_inline_style_block(self) -> None:
        out, coll = _render(
            "{% load next_static %}{% #use_style %}body{color:red}{% /use_style %}"
        )
        assert out == ""
        styles = coll.assets_in_slot("styles")
        assert len(styles) == 1
        assert styles[0].inline == "body{color:red}"
        assert styles[0].url == ""

    def test_inline_script_block(self) -> None:
        out, coll = _render(
            "{% load next_static %}{% #use_script %}window.x=1;{% /use_script %}"
        )
        assert out == ""
        scripts = coll.assets_in_slot("scripts")
        assert len(scripts) == 1
        assert scripts[0].inline == "window.x=1;"

    def test_block_interpolates_context(self) -> None:
        out, coll = _render(
            "{% load next_static %}"
            "{% #use_script %}window.user = '{{ user }}';{% /use_script %}",
            {"user": "alice"},
        )
        assert out == ""
        scripts = coll.assets_in_slot("scripts")
        assert scripts[0].inline == "window.user = 'alice';"

    def test_blank_block_body_is_ignored(self) -> None:
        out, coll = _render(
            "{% load next_static %}{% #use_style %}   \n   {% /use_style %}"
        )
        assert out == ""
        assert coll.assets_in_slot("styles") == []

    def test_block_without_collector_noop(self) -> None:
        template = Template(
            "{% load next_static %}{% #use_style %}body{}{% /use_style %}"
        )
        assert template.render(Context({})) == ""


class TestPrependOrdering:
    """use_style/use_script insert before co-located files via collector prepend."""

    def test_use_style_lands_at_front(self) -> None:
        _, coll = _render(
            '{% load next_static %}{% use_style "https://cdn/a.css" %}'
            '{% use_style "https://cdn/b.css" %}'
        )
        urls = [a.url for a in coll.assets_in_slot("styles")]
        assert urls == ["https://cdn/a.css", "https://cdn/b.css"]
