from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django import forms
from django.http import HttpRequest

from next.forms.widgets import ComponentWidget, bind_component_widgets
from next.seeding import RenderFrame
from next.testing import override_component_backends


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


_FIELD_TEMPLATE = (
    '<input name="{{ name }}" value="{{ value }}" placeholder="{{ placeholder }}">'
)

_WRAPPED_TEMPLATE = (
    '{% load components %}<label>{% component "field" name=name value=value %}</label>'
)


class _MultiFieldForm(forms.Form):
    one = forms.CharField(widget=ComponentWidget("field", placeholder="one"))
    two = forms.CharField(widget=ComponentWidget("field", placeholder="two"))
    three = forms.CharField(widget=ComponentWidget("field", placeholder="three"))
    four = forms.CharField(widget=ComponentWidget("field", placeholder="four"))
    five = forms.CharField(widget=ComponentWidget("field", placeholder="five"))


@pytest.fixture()
def widget_anchor(tmp_path: Path) -> Iterator[Path]:
    """Register a simple `field` component and yield the page anchor path."""
    root = tmp_path / "_components"
    root.mkdir()
    (root / "field.djx").write_text(_FIELD_TEMPLATE)
    with override_component_backends(
        {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
    ):
        yield tmp_path / "page.djx"


@pytest.fixture()
def nesting_anchor(tmp_path: Path) -> Iterator[Path]:
    """Register a `wrapped` component calling `field`, and yield the page anchor.

    The registry of `widget_anchor` stays single-entry, so its runs compare to a base.
    """
    root = tmp_path / "_components"
    root.mkdir()
    (root / "field.djx").write_text(_FIELD_TEMPLATE)
    (root / "wrapped.djx").write_text(_WRAPPED_TEMPLATE)
    with override_component_backends(
        {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
    ):
        yield tmp_path / "page.djx"


class TestBenchComponentWidgetRender:
    @pytest.mark.benchmark(group="forms.widgets")
    def test_render_cold_lookup(self, widget_anchor: Path, benchmark) -> None:
        """Standalone render without a request hits the registry on every call."""
        widget = ComponentWidget("field", placeholder="slug")
        widget._frame = RenderFrame(template_path=widget_anchor)
        benchmark(widget.render, "slug", "value", {"id": "id_slug"})

    @pytest.mark.benchmark(group="forms.widgets")
    def test_render_warm_request_cache(self, widget_anchor: Path, benchmark) -> None:
        """Bound render with a request serves lookups from the per-request cache."""
        widget = ComponentWidget("field", placeholder="slug")
        widget._frame = RenderFrame(template_path=widget_anchor, request=HttpRequest())
        widget.render("slug", "value", {"id": "id_slug"})
        benchmark(widget.render, "slug", "value", {"id": "id_slug"})

    @pytest.mark.benchmark(group="forms.widgets")
    def test_render_nested_component_warm(
        self, nesting_anchor: Path, benchmark
    ) -> None:
        """A widget component composing a nested one pays for the seeded frame."""
        widget = ComponentWidget("wrapped", placeholder="slug")
        widget._frame = RenderFrame(template_path=nesting_anchor, request=HttpRequest())
        widget.render("slug", "value", {"id": "id_slug"})
        benchmark(widget.render, "slug", "value", {"id": "id_slug"})


class TestBenchBindComponentWidgets:
    @pytest.mark.benchmark(group="forms.widgets")
    def test_bind_multi_field_form(self, widget_anchor: Path, benchmark) -> None:
        """Inject scope path, request, and errors across five component widgets."""
        form = _MultiFieldForm(data={})

        def run() -> None:
            bind_component_widgets(
                form,
                template_path=widget_anchor,
                request=HttpRequest(),
                with_errors=True,
            )

        benchmark(run)
