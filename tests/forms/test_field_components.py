from collections.abc import Generator
from datetime import date
from pathlib import Path
from unittest import mock

import pytest
from django import forms as django_forms
from django.core.checks import Warning as DjangoWarning
from django.utils.safestring import SafeString

from next.components import FileComponentsBackend, components_manager
from next.forms.checks import (
    check_component_widget_components,
    check_component_widget_field_types,
)
from next.forms.manager import form_action_manager
from next.forms.widgets import ComponentWidget, bind_component_widgets
from next.static import StaticCollector


_ECHO_TEMPLATE = (
    "<div>"
    "name={{ name }} "
    "value={{ value }} "
    "placeholder={{ placeholder }} "
    "rows={{ rows }} "
    "id={{ id }} "
    "required={{ required }} "
    "data-x={{ data_x }} "
    "aria-invalid={{ aria_invalid }} "
    "aria-describedby={{ aria_describedby }} "
    "raw-attrs={{ attrs }} "
    "errors={{ errors|join:',' }}"
    "</div>"
)


@pytest.fixture()
def echo_component(tmp_path: Path) -> Generator[Path, None, None]:
    """Register an `echo` component that prints context vars, yield the anchor path."""
    root = tmp_path / "_components"
    root.mkdir()
    (root / "echo.djx").write_text(_ECHO_TEMPLATE)

    config = {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
    backend = FileComponentsBackend(config)
    previous = list(components_manager._backends)
    components_manager._backends.clear()
    components_manager._backends.append(backend)
    try:
        yield tmp_path / "page.djx"
    finally:
        components_manager._backends.clear()
        components_manager._backends.extend(previous)


@pytest.fixture()
def echo_box_component(tmp_path: Path) -> Generator[Path, None, None]:
    """Register a composite `echo_box` component with a co-located component.css."""
    root = tmp_path / "_components"
    comp_dir = root / "echo_box"
    comp_dir.mkdir(parents=True)
    (comp_dir / "component.djx").write_text("<div>name={{ name }}</div>")
    (comp_dir / "component.css").write_text(".echo-box {}")

    config = {"DIRS": [str(root)], "COMPONENTS_DIR": "_components"}
    backend = FileComponentsBackend(config)
    previous = list(components_manager._backends)
    components_manager._backends.clear()
    components_manager._backends.append(backend)
    try:
        yield tmp_path / "page.djx"
    finally:
        components_manager._backends.clear()
        components_manager._backends.extend(previous)


def _echo_form(widget: ComponentWidget) -> type[django_forms.Form]:
    """Build a one-field plain Django form whose field uses `widget`."""

    class _EchoForm(django_forms.Form):
        field = django_forms.CharField(widget=widget, required=True)

    return _EchoForm


class TestComponentWidgetInit:
    """`ComponentWidget.__init__` stores name and extra render kwargs."""

    def test_stores_component_name_and_kwargs(self) -> None:
        widget = ComponentWidget("input", placeholder="URL slug", rows=12)
        assert widget.component_name == "input"
        assert widget.extra_kwargs == {"placeholder": "URL slug", "rows": 12}

    def test_no_kwargs_yields_empty_extra(self) -> None:
        widget = ComponentWidget("input")
        assert widget.extra_kwargs == {}

    def test_constructor_attrs_stored_on_widget(self) -> None:
        widget = ComponentWidget("input", attrs={"data-x": "1"})
        assert widget.attrs == {"data-x": "1"}

    def test_constructor_attrs_excluded_from_extra_kwargs(self) -> None:
        widget = ComponentWidget("input", attrs={"data-x": "1"}, placeholder="p")
        assert "attrs" not in widget.extra_kwargs
        assert widget.extra_kwargs == {"placeholder": "p"}


class TestComponentWidgetRender:
    """`ComponentWidget.render` resolves and renders the named component."""

    def test_returns_safestring_with_context_values(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo", placeholder="URL slug", rows=12)
        widget._template_path = echo_component
        html = widget.render("slug", "hello", attrs={"id": "id_slug", "required": True})
        assert isinstance(html, SafeString)
        assert "name=slug" in html
        assert "value=hello" in html

    def test_spreads_extra_kwargs_into_context(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo", placeholder="URL slug", rows=12)
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={})
        assert "placeholder=URL slug" in html
        assert "rows=12" in html

    def test_spreads_attrs_to_top_level(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={"id": "id_slug", "required": True})
        assert "id=id_slug" in html
        assert "required=True" in html

    def test_errors_default_to_empty(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={})
        assert "errors=" in html

    def test_injected_errors_reach_context(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        widget._errors = ["bad value"]
        html = widget.render("slug", "v", attrs={})
        assert "errors=bad value" in html

    def test_base_dir_fallback_without_template_path(
        self, echo_component: Path
    ) -> None:
        # No _template_path set: render must fall back to settings.BASE_DIR. The
        # echo component is registered as a global root so it resolves anyway.
        widget = ComponentWidget("echo")
        html = widget.render("slug", "v", attrs={})
        assert "name=slug" in html

    def test_unregistered_component_raises_runtime_error(
        self, echo_component: Path
    ) -> None:
        widget = ComponentWidget("does_not_exist")
        widget._template_path = echo_component
        with pytest.raises(RuntimeError, match="is not registered"):
            widget.render("slug", "v", attrs=None)


class TestComponentWidgetConstructorAttrs:
    """Constructor `attrs=` merge into render output, render-time attrs win."""

    def test_constructor_attrs_reach_context(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo", attrs={"data-x": "1"})
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={})
        assert "data-x=1" in html

    def test_render_attrs_override_constructor_on_collision(
        self, echo_component: Path
    ) -> None:
        widget = ComponentWidget("echo", attrs={"data-x": "ctor"})
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={"data-x": "render"})
        assert "data-x=render" in html
        assert "data-x=ctor" not in html

    def test_constructor_and_render_attrs_both_present(
        self, echo_component: Path
    ) -> None:
        widget = ComponentWidget("echo", attrs={"data-x": "1"})
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={"id": "id_slug"})
        assert "data-x=1" in html
        assert "id=id_slug" in html


class TestComponentWidgetAriaAliases:
    """Hyphenated attr keys are aliased to underscore template vars."""

    def test_hyphenated_keys_aliased_to_underscore_vars(
        self, echo_component: Path
    ) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render(
            "slug",
            "v",
            attrs={"aria-invalid": "true", "aria-describedby": "id_foo_error"},
        )
        assert "aria-invalid=true" in html
        assert "aria-describedby=id_foo_error" in html

    def test_raw_attrs_mapping_keeps_hyphenated_originals(
        self, echo_component: Path
    ) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render("slug", "v", attrs={"aria-invalid": "true"})
        assert "&#x27;aria-invalid&#x27;: &#x27;true&#x27;" in html

    def test_underscore_form_present_blocks_alias(self, echo_component: Path) -> None:
        # data_x already exists, so the data-x value must not overwrite it.
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render(
            "slug", "v", attrs={"data-x": "hyphen", "data_x": "underscore"}
        )
        assert "data-x=underscore" in html


class TestComponentWidgetFormatValue:
    """`render` runs value through the inherited `format_value`."""

    def test_non_string_value_formatted_to_string(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render("when", date(2024, 1, 2), attrs={})
        assert "value=2024-01-02" in html

    def test_int_value_formatted_to_string(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render("count", 7, attrs={})
        assert "value=7" in html

    def test_empty_value_becomes_none(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        widget._template_path = echo_component
        html = widget.render("slug", "", attrs={})
        assert "value=None" in html


class TestComponentWidgetAssetCollection:
    """`render` discovers co-located component assets when a collector is bound."""

    def _render_with_collector(
        self,
        component_name: str,
        anchor: Path,
        collector: StaticCollector | None,
    ) -> None:
        widget = ComponentWidget(component_name)
        widget._template_path = anchor
        if collector is not None:
            widget._static_collector = collector
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/components/echo_box.css",
        ):
            widget.render("slug", "v", attrs={})

    def test_composite_component_collects_assets(
        self, echo_box_component: Path
    ) -> None:
        collector = StaticCollector()
        self._render_with_collector("echo_box", echo_box_component, collector)
        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        assert "/static/next/components/echo_box.css" in style_urls

    def test_simple_component_collects_nothing(self, echo_component: Path) -> None:
        collector = StaticCollector()
        self._render_with_collector("echo", echo_component, collector)
        assert collector.assets_in_slot("styles") == []
        assert collector.assets_in_slot("scripts") == []

    def test_no_collector_does_not_collect(self, echo_box_component: Path) -> None:
        # No _static_collector bound: render is a no-op for asset discovery.
        widget = ComponentWidget("echo_box")
        widget._template_path = echo_box_component
        html = widget.render("slug", "v", attrs={})
        assert "name=slug" in html


class TestBindComponentWidgets:
    """`bind_component_widgets` injects scope path, request, and errors."""

    def test_sets_template_path_and_request(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        form = _echo_form(widget)()
        request = object()
        bind_component_widgets(form, template_path=echo_component, request=request)
        bound_widget = form.fields["field"].widget
        assert bound_widget._template_path == echo_component
        assert bound_widget._request is request

    def test_sets_static_collector(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        form = _echo_form(widget)()
        collector = StaticCollector()
        bind_component_widgets(form, template_path=echo_component, collector=collector)
        bound_widget = form.fields["field"].widget
        assert bound_widget._static_collector is collector

    def test_collector_defaults_to_none(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        form = _echo_form(widget)()
        bind_component_widgets(form, template_path=echo_component)
        bound_widget = form.fields["field"].widget
        assert bound_widget._static_collector is None

    def test_with_errors_sets_field_errors(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        # Bound but invalid: a required field left blank yields non-empty errors.
        form = _echo_form(widget)(data={"field": ""})
        bind_component_widgets(form, template_path=echo_component, with_errors=True)
        bound_widget = form.fields["field"].widget
        assert bound_widget._errors == form["field"].errors
        assert list(bound_widget._errors)

    def test_without_errors_leaves_errors_unset(self, echo_component: Path) -> None:
        widget = ComponentWidget("echo")
        form = _echo_form(widget)(data={"field": ""})
        bind_component_widgets(form, template_path=echo_component, with_errors=False)
        bound_widget = form.fields["field"].widget
        assert not hasattr(bound_widget, "_errors")

    def test_skips_non_component_widgets(self, echo_component: Path) -> None:
        class _MixedForm(django_forms.Form):
            plain = django_forms.CharField(widget=django_forms.TextInput())
            comp = django_forms.CharField(widget=ComponentWidget("echo"))

        form = _MixedForm()
        bind_component_widgets(form, template_path=echo_component)
        plain_widget = form.fields["plain"].widget
        comp_widget = form.fields["comp"].widget
        assert not hasattr(plain_widget, "_template_path")
        assert comp_widget._template_path == echo_component


class TestCheckComponentWidgetComponents:
    """`check_component_widget_components` emits W054 for unresolved components."""

    def _register_form(
        self, name: str, form_class: type[django_forms.Form], file_path: str
    ) -> None:
        form_action_manager.default_backend.register_action(
            name,
            form_class=form_class,
            file_path=file_path,
            scope="page",
        )

    def test_missing_component_yields_w054(self, echo_component: Path) -> None:
        class _MissingForm(django_forms.Form):
            field = django_forms.CharField(widget=ComponentWidget("nope"))

        self._register_form("missing_form", _MissingForm, str(echo_component))
        warnings = check_component_widget_components()
        assert len(warnings) == 1
        assert isinstance(warnings[0], DjangoWarning)
        assert warnings[0].id == "next.W054"
        assert "nope" in warnings[0].msg

    def test_resolved_component_yields_no_warning(self, echo_component: Path) -> None:
        class _OkForm(django_forms.Form):
            field = django_forms.CharField(widget=ComponentWidget("echo"))

        self._register_form("ok_form", _OkForm, str(echo_component))
        assert check_component_widget_components() == []

    def test_same_missing_component_reported_once(self, echo_component: Path) -> None:
        class _TwoFieldForm(django_forms.Form):
            one = django_forms.CharField(widget=ComponentWidget("nope"))
            two = django_forms.CharField(widget=ComponentWidget("nope"))

        self._register_form("two_field_form", _TwoFieldForm, str(echo_component))
        warnings = check_component_widget_components()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W054"

    def test_form_without_component_widget_is_clean(self, echo_component: Path) -> None:
        class _PlainForm(django_forms.Form):
            field = django_forms.CharField(widget=django_forms.TextInput())

        self._register_form("plain_form", _PlainForm, str(echo_component))
        assert check_component_widget_components() == []

    def test_meta_with_no_form_class_is_skipped(self) -> None:
        form_action_manager.default_backend.register_action(
            "formless",
            handler=lambda *_: None,
            file_path="/fake/page.py",
            scope="page",
        )
        assert check_component_widget_components() == []

    def test_backend_without_registry_falls_back(self, monkeypatch) -> None:
        # A backend object exposing no _registry must not raise: the check reads
        # an empty mapping through getattr and reports nothing.
        class _BareBackend:
            pass

        monkeypatch.setattr(form_action_manager, "_backends", [_BareBackend()])
        assert check_component_widget_components() == []


class TestCheckComponentWidgetFieldTypes:
    """`check_component_widget_field_types` emits W055 for unsupported field types."""

    def _register_form(
        self, name: str, form_class: type[django_forms.Form], file_path: str
    ) -> None:
        form_action_manager.default_backend.register_action(
            name,
            form_class=form_class,
            file_path=file_path,
            scope="page",
        )

    def test_file_field_yields_w055(self, echo_component: Path) -> None:
        class _FileForm(django_forms.Form):
            upload = django_forms.FileField(widget=ComponentWidget("echo"))

        self._register_form("file_form", _FileForm, str(echo_component))
        warnings = check_component_widget_field_types()
        assert len(warnings) == 1
        assert isinstance(warnings[0], DjangoWarning)
        assert warnings[0].id == "next.W055"
        assert "FileField" in warnings[0].msg

    def test_multi_value_field_yields_w055(self, echo_component: Path) -> None:
        class _MultiForm(django_forms.Form):
            combo = django_forms.MultiValueField(
                fields=(django_forms.CharField(), django_forms.CharField()),
                widget=ComponentWidget("echo"),
                require_all_fields=False,
            )

        self._register_form("multi_form", _MultiForm, str(echo_component))
        warnings = check_component_widget_field_types()
        assert len(warnings) == 1
        assert warnings[0].id == "next.W055"
        assert "MultiValueField" in warnings[0].msg

    def test_char_field_yields_no_warning(self, echo_component: Path) -> None:
        class _CharForm(django_forms.Form):
            slug = django_forms.CharField(widget=ComponentWidget("echo"))

        self._register_form("char_form", _CharForm, str(echo_component))
        assert check_component_widget_field_types() == []

    def test_textarea_widget_char_field_yields_no_warning(
        self, echo_component: Path
    ) -> None:
        class _BodyForm(django_forms.Form):
            body = django_forms.CharField(widget=django_forms.Textarea())

        self._register_form("body_form", _BodyForm, str(echo_component))
        assert check_component_widget_field_types() == []

    def test_file_field_without_component_widget_is_clean(
        self, echo_component: Path
    ) -> None:
        class _PlainFileForm(django_forms.Form):
            upload = django_forms.FileField(widget=django_forms.ClearableFileInput())

        self._register_form("plain_file_form", _PlainFileForm, str(echo_component))
        assert check_component_widget_field_types() == []

    def test_meta_with_no_form_class_is_skipped(self) -> None:
        form_action_manager.default_backend.register_action(
            "formless_types",
            handler=lambda *_: None,
            file_path="/fake/page.py",
            scope="page",
        )
        assert check_component_widget_field_types() == []
