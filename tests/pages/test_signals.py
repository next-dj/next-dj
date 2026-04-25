"""Tests for next.pages.signals: template_loaded, context_registered, page_rendered."""

from pathlib import Path
from typing import Any

from next.pages import Page
from next.pages.registry import PageContextRegistry


class TestTemplateLoadedSignal:
    """``template_loaded`` fires when a template is registered on a Page."""

    def test_fires_on_register_template(
        self, capture_template_loaded: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Registering a template emits ``template_loaded``."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "<h1>Hello</h1>")
        assert len(capture_template_loaded) == 1

    def test_sender_is_page_class(
        self, capture_template_loaded: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """``template_loaded`` sender is the ``Page`` class."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "<h1>Hello</h1>")
        assert capture_template_loaded[0]["sender"] is Page

    def test_fires_once_per_register(
        self, capture_template_loaded: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Each ``register_template`` call emits exactly one event."""
        page_inst = Page()
        page_inst.register_template(tmp_path / "a.py", "template A")
        page_inst.register_template(tmp_path / "b.py", "template B")
        assert len(capture_template_loaded) == 2

    def test_event_contains_file_path(
        self, capture_template_loaded: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """The emitted event carries the ``file_path`` kwarg."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "content")
        assert "file_path" in capture_template_loaded[0]
        assert capture_template_loaded[0]["file_path"] == page_file

    def test_does_not_fire_without_register(
        self, capture_template_loaded: list[dict[str, Any]]
    ) -> None:
        """Creating a ``Page`` without registering templates does not emit the signal."""
        Page()
        assert len(capture_template_loaded) == 0


class TestContextRegisteredSignal:
    """``context_registered`` fires when a context function is registered."""

    def test_fires_on_register_context(
        self, capture_context_registered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Registering a context function emits ``context_registered``."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst._context_manager.register_context(page_file, "key", lambda: "value")
        assert len(capture_context_registered) == 1

    def test_sender_is_page_context_registry_class(
        self, capture_context_registered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """``context_registered`` sender is the ``PageContextRegistry`` class."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst._context_manager.register_context(page_file, "key", lambda: "value")
        assert capture_context_registered[0]["sender"] is PageContextRegistry

    def test_fires_once_per_registration(
        self, capture_context_registered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Each ``register_context`` call emits exactly one event."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst._context_manager.register_context(page_file, "key1", lambda: "v1")
        page_inst._context_manager.register_context(page_file, "key2", lambda: "v2")
        assert len(capture_context_registered) == 2

    def test_event_contains_file_path(
        self, capture_context_registered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """The emitted event carries the ``file_path`` kwarg."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst._context_manager.register_context(page_file, "k", lambda: "v")
        assert "file_path" in capture_context_registered[0]
        assert capture_context_registered[0]["file_path"] == page_file

    def test_event_contains_key(
        self, capture_context_registered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """The emitted event carries the ``key`` kwarg."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst._context_manager.register_context(page_file, "my_key", lambda: "v")
        assert capture_context_registered[0]["key"] == "my_key"

    def test_does_not_fire_without_registration(
        self, capture_context_registered: list[dict[str, Any]]
    ) -> None:
        """Creating a ``Page`` without registering context does not emit the signal."""
        Page()
        assert len(capture_context_registered) == 0


class TestPageRenderedSignal:
    """``page_rendered`` fires after a page is rendered."""

    def test_fires_on_render(
        self, capture_page_rendered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Calling ``page.render()`` emits ``page_rendered``."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "<p>{{ x }}</p>")
        capture_page_rendered.clear()  # clear the template_loaded-induced events
        page_inst.render(page_file, x="hello")
        assert len(capture_page_rendered) == 1

    def test_sender_is_page_class(
        self, capture_page_rendered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """``page_rendered`` sender is the ``Page`` class."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "static")
        capture_page_rendered.clear()
        page_inst.render(page_file)
        assert capture_page_rendered[0]["sender"] is Page

    def test_fires_once_per_render(
        self, capture_page_rendered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Each ``render()`` call emits exactly one event."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "content")
        capture_page_rendered.clear()
        page_inst.render(page_file)
        page_inst.render(page_file)
        assert len(capture_page_rendered) == 2

    def test_event_contains_file_path(
        self, capture_page_rendered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """The emitted event carries the ``file_path`` kwarg."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "content")
        capture_page_rendered.clear()
        page_inst.render(page_file)
        assert "file_path" in capture_page_rendered[0]
        assert capture_page_rendered[0]["file_path"] == page_file

    def test_event_contains_enriched_payload(
        self, capture_page_rendered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """The event carries duration_ms, styles/scripts counts, and context keys."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "<p>{{ greeting }}</p>")
        capture_page_rendered.clear()
        page_inst.render(page_file, greeting="hi")
        event = capture_page_rendered[0]
        assert isinstance(event["duration_ms"], float)
        assert event["duration_ms"] >= 0
        assert event["styles_count"] == 0
        assert event["scripts_count"] == 0
        assert "greeting" in event["context_keys"]

    def test_does_not_fire_without_render(
        self, capture_page_rendered: list[dict[str, Any]], tmp_path: Path
    ) -> None:
        """Registering a template without rendering does not emit ``page_rendered``."""
        page_inst = Page()
        page_file = tmp_path / "page.py"
        page_inst.register_template(page_file, "content")
        # capture_page_rendered only tracks page_rendered, not template_loaded
        assert len(capture_page_rendered) == 0
