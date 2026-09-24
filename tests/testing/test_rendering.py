from pathlib import Path
from unittest.mock import patch

import pytest
from django.test import RequestFactory

from next.components import ComponentInfo, FileComponentsBackend, components_manager
from next.pages.manager import page
from next.seeding import TEMPLATE_PATH_KEY
from next.static import StaticCollector
from next.testing import override_component_backends
from next.testing.rendering import render_component_by_name, render_page
from tests.support.components import components_config


class TestRenderPage:
    """`render_page` forwards to `page.render` with a synthetic request."""

    def test_renders_registered_template(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<p>hello</p>")
        assert "<p>hello</p>" in render_page(page_file)

    def test_accepts_str_path(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<p>plain</p>")
        assert "<p>plain</p>" in render_page(str(page_file))

    def test_forwards_url_kwargs(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<p>{{ slug }}</p>")
        assert "<p>abc</p>" in render_page(page_file, slug="abc")

    def test_accepts_custom_request(self, tmp_path: Path) -> None:
        page_file = tmp_path / "page.py"
        page.register_template(page_file, "<i>{{ request.path }}</i>")
        req = RequestFactory().get("/custom/")
        assert "/custom/" in render_page(page_file, req)


class TestRenderComponentByName:
    """`render_component_by_name` resolves scoping and renders the component."""

    def test_raises_when_component_not_visible(self, tmp_path: Path) -> None:
        with pytest.raises(LookupError, match="not visible"):
            render_component_by_name("missing", at=tmp_path / "page.djx")

    def test_renders_visible_component(self, tmp_path: Path) -> None:
        root = tmp_path / "_components"
        root.mkdir()
        (root / "greeter.djx").write_text("<b>{{ name }}</b>")

        config = {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
        backend = FileComponentsBackend(config)
        previous = components_manager._backends
        previously_loaded = components_manager._loaded
        components_manager._backends = [backend]
        components_manager._loaded = True
        try:
            html = render_component_by_name(
                "greeter", at=tmp_path / "page.djx", context={"name": "World"}
            )
        finally:
            components_manager._backends = previous
            components_manager._loaded = previously_loaded
        assert "<b>World</b>" in html

    def test_accepts_str_anchor(self, tmp_path: Path) -> None:
        with pytest.raises(LookupError, match="Component not visible from"):
            render_component_by_name("nope", at=str(tmp_path / "page.djx"))

    @staticmethod
    def _info(tmp_path: Path, module_path: Path | None) -> ComponentInfo:
        (tmp_path / "widget.djx").write_text("[{{ current_component_module_path }}]")
        return ComponentInfo(
            name="widget",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "widget.djx",
            module_path=module_path,
            is_simple=True,
        )

    def test_sets_component_module_path_from_info(self, tmp_path: Path) -> None:
        module_path = tmp_path / "widget" / "component.py"
        info = self._info(tmp_path, module_path)
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name("widget", at=tmp_path / "page.djx")
        assert f"[{module_path}]" in html

    def test_module_less_component_sets_none(self, tmp_path: Path) -> None:
        info = self._info(tmp_path, None)
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name("widget", at=tmp_path / "page.djx")
        assert "[None]" in html

    def test_renderer_stamp_overrides_caller_value(self, tmp_path: Path) -> None:
        module_path = tmp_path / "widget" / "component.py"
        info = self._info(tmp_path, module_path)
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name(
                "widget",
                at=tmp_path / "page.djx",
                context={"current_component_module_path": "caller-anchor"},
            )
        assert f"[{module_path}]" in html
        assert "caller-anchor" not in html


class TestRenderComponentByNamePropGuard:
    """`props` stands in for a tag call site, `context` for the page scope."""

    @staticmethod
    def _composite(tmp_path: Path, returned: str) -> ComponentInfo:
        """Build a composite component whose keyless context returns `returned`."""
        (tmp_path / "component.djx").write_text(
            "<div>title={{ title }} hint={{ hint }}</div>"
        )
        (tmp_path / "component.py").write_text(
            "from next.components import component\n\n\n"
            "@component.context\n"
            "def extra():\n"
            f"    return {returned}\n"
        )
        return ComponentInfo(
            name="card",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "component.djx",
            module_path=(tmp_path / "component.py").resolve(),
            is_simple=False,
        )

    def test_caller_prop_raises(self, tmp_path: Path) -> None:
        info = self._composite(tmp_path, '{"title": "from context"}')
        with (
            patch.object(components_manager, "get_component", return_value=info),
            pytest.raises(ValueError, match="context returns 'title'"),
        ):
            render_component_by_name(
                "card", at=tmp_path / "page.djx", props={"title": "from caller"}
            )

    def test_ambient_context_key_is_shadowed(self, tmp_path: Path) -> None:
        info = self._composite(tmp_path, '{"title": "from context"}')
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name(
                "card", at=tmp_path / "page.djx", context={"title": "from page"}
            )
        assert "title=from context" in html

    def test_props_win_over_ambient_context(self, tmp_path: Path) -> None:
        info = self._composite(tmp_path, '{"hint": "merged"}')
        with patch.object(components_manager, "get_component", return_value=info):
            html = render_component_by_name(
                "card",
                at=tmp_path / "page.djx",
                context={"title": "from page"},
                props={"title": "from caller"},
            )
        assert "title=from caller" in html
        assert "hint=merged" in html


_COMPOSING_TEMPLATE = '{% load components %}<div>{% component "inner" %}</div>'


class TestRenderComponentByNameFrame:
    """`render_component_by_name` seeds the ambient frame it searched from."""

    def test_a_nested_component_resolves_from_the_anchor(self, tmp_path: Path) -> None:
        root = tmp_path / "_components"
        root.mkdir()
        (root / "inner.djx").write_text("<span>inner</span>")
        (root / "outer.djx").write_text(_COMPOSING_TEMPLATE)

        with override_component_backends(components_config(root)):
            html = render_component_by_name("outer", at=tmp_path / "page.djx")

        assert "<span>inner</span>" in html

    def test_an_explicit_anchor_in_context_wins(self, tmp_path: Path) -> None:
        root = tmp_path / "_components"
        root.mkdir()
        (root / "echo_path.djx").write_text("<i>{{ current_template_path }}</i>")

        with override_component_backends(components_config(root)):
            html = render_component_by_name(
                "echo_path",
                at=tmp_path / "page.djx",
                context={TEMPLATE_PATH_KEY: tmp_path / "other.djx"},
            )

        assert "other.djx</i>" in html

    def test_a_bound_collector_catches_the_nested_assets(self, tmp_path: Path) -> None:
        root = tmp_path / "_components"
        inner = root / "inner"
        inner.mkdir(parents=True)
        (inner / "component.djx").write_text("<span>inner</span>")
        (inner / "component.css").write_text(".inner {}")
        (root / "outer.djx").write_text(_COMPOSING_TEMPLATE)
        collector = StaticCollector()

        with (
            override_component_backends(components_config(root)),
            patch(
                "next.static.backends.staticfiles_storage.url",
                return_value="/static/next/components/inner.css",
            ),
        ):
            render_component_by_name(
                "outer", at=tmp_path / "page.djx", collector=collector
            )

        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        assert "/static/next/components/inner.css" in style_urls

    def test_a_bound_collector_catches_the_assets_of_the_component_itself(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "_components"
        card = root / "card"
        card.mkdir(parents=True)
        (card / "component.djx").write_text("<span>card</span>")
        (card / "component.css").write_text(".card {}")
        collector = StaticCollector()

        with (
            override_component_backends(components_config(root)),
            patch(
                "next.static.backends.staticfiles_storage.url",
                return_value="/static/next/components/card.css",
            ),
        ):
            render_component_by_name(
                "card", at=tmp_path / "page.djx", collector=collector
            )

        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        assert "/static/next/components/card.css" in style_urls

    def test_the_page_anchor_reaches_a_page_scoped_action(self, tmp_path: Path) -> None:
        """A body holding `{% action_url %}` needs the page module, not a raw key."""
        root = tmp_path / "_components"
        root.mkdir()
        (root / "anchor.djx").write_text("<i>{{ current_page_module_path }}</i>")
        page_module = tmp_path / "page.py"

        with override_component_backends(components_config(root)):
            html = render_component_by_name(
                "anchor", at=tmp_path / "page.djx", page_module_path=page_module
            )

        assert f"<i>{page_module}</i>" in html
