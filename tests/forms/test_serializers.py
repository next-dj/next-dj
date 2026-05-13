from typing import ClassVar

import pytest
from django import forms as django_forms
from django.contrib.admin.widgets import RelatedFieldWidgetWrapper
from django.forms import BoundField, formset_factory

from next.forms import (
    FormSectionSpec,
    FormsetRowSpec,
    FormsetSpec,
    FormSpec,
    field_spec,
    form_spec,
    formset_spec,
)


def _related_wrapper_around(
    inner_widget: django_forms.Widget,
) -> RelatedFieldWidgetWrapper:
    """Construct a `RelatedFieldWidgetWrapper` without admin-site plumbing.

    The real `__init__` requires `rel`, an admin site, and permission flags.
    The wrapper unwrap path in `field_spec` only reads `widget.widget`, so
    bypassing `__init__` keeps the test independent from admin internals.
    """
    wrapper = RelatedFieldWidgetWrapper.__new__(RelatedFieldWidgetWrapper)
    wrapper.widget = inner_widget
    wrapper.attrs = {}
    return wrapper


class _SampleForm(django_forms.Form):
    name = django_forms.CharField(max_length=20, required=True)
    email = django_forms.EmailField(required=False)
    notes = django_forms.CharField(widget=django_forms.Textarea, required=False)
    agree = django_forms.BooleanField(required=False)
    plan = django_forms.ChoiceField(choices=[("a", "A"), ("b", "B")], required=False)
    tags = django_forms.MultipleChoiceField(
        choices=[("x", "X"), ("y", "Y")], required=False
    )
    born = django_forms.DateField(required=False)


class TestFieldSpec:
    """`field_spec` classifies widgets and pre-computes value/selected."""

    @pytest.mark.parametrize(
        ("field_name", "expected_kind", "expected_input_type"),
        [
            ("name", "input", "text"),
            ("email", "input", "email"),
            ("notes", "textarea", ""),
            ("agree", "checkbox", ""),
            ("plan", "select", ""),
            ("tags", "select_multi", ""),
            ("born", "input", "text"),
        ],
        ids=("text", "email", "textarea", "checkbox", "select", "multi", "date"),
    )
    def test_classifies_widget_kind_and_input_type(
        self, field_name, expected_kind, expected_input_type
    ) -> None:
        spec = field_spec(_SampleForm()[field_name])
        assert spec.kind == expected_kind
        assert spec.input_type == expected_input_type

    def test_records_bound_value_and_is_extra_flag(self) -> None:
        form = _SampleForm(initial={"name": "Alice"})
        spec = field_spec(form["name"], is_extra=True)
        assert spec.value == "Alice"
        assert spec.is_extra is True
        assert isinstance(spec.bound, BoundField)

    def test_multi_select_pre_computes_selected_strs(self) -> None:
        form = _SampleForm(initial={"tags": ["x", "y"]})
        spec = field_spec(form["tags"])
        assert spec.kind == "select_multi"
        assert spec.selected == ("x", "y")

    def test_multi_select_scalar_value_wraps_into_tuple(self) -> None:
        form = _SampleForm(initial={"tags": "x"})
        spec = field_spec(form["tags"])
        assert spec.selected == ("x",)

    def test_multi_select_missing_value_yields_empty_selected(self) -> None:
        spec = field_spec(_SampleForm()["tags"])
        assert spec.selected == ()

    def test_unknown_widget_falls_back_to_input_kind(self) -> None:
        class _CustomWidget(django_forms.Widget):
            input_type = "color"

        class _F(django_forms.Form):
            shade = django_forms.CharField(widget=_CustomWidget())

        spec = field_spec(_F()["shade"])
        assert spec.kind == "input"
        assert spec.input_type == "color"

    def test_related_widget_wrapper_unwraps_inner_widget(self) -> None:
        wrapper = _related_wrapper_around(django_forms.SelectMultiple())

        class _F(django_forms.Form):
            picks = django_forms.MultipleChoiceField(
                choices=[("a", "A")], widget=wrapper
            )

        spec = field_spec(_F()["picks"])
        assert spec.kind == "select_multi"


class _Row(django_forms.Form):
    title = django_forms.CharField(max_length=50)
    note = django_forms.CharField(required=False)


_RowFormset = formset_factory(_Row, extra=1, can_delete=True)
_NoDeleteFormset = formset_factory(_Row, extra=1, can_delete=False)


class TestFormsetSpec:
    """`formset_spec` rolls hidden inputs, flags extra rows, surfaces errors."""

    def test_basic_shape_for_standalone_formset(self) -> None:
        formset = _RowFormset(initial=[{"title": "first"}])
        spec = formset_spec(formset)
        assert isinstance(spec, FormsetSpec)
        assert spec.prefix == "form"
        assert spec.verbose_name_plural == ""
        assert spec.can_delete is True
        assert spec.non_form_errors == ()
        assert len(spec.rows) == 2

    def test_row_marks_blank_extra_as_is_extra(self) -> None:
        formset = _RowFormset(initial=[{"title": "first"}])
        spec = formset_spec(formset)
        first, extra = spec.rows
        assert first.is_extra is False
        assert extra.is_extra is True

    def test_row_hidden_html_is_string(self) -> None:
        formset = _RowFormset(initial=[{"title": "first"}])
        spec = formset_spec(formset)
        assert isinstance(spec.rows[0].hidden_html, str)

    def test_row_delete_field_exposed_when_can_delete(self) -> None:
        formset = _RowFormset(initial=[{"title": "first"}])
        spec = formset_spec(formset)
        first = spec.rows[0]
        assert first.delete_field is not None
        assert first.delete_field.name == "DELETE"

    def test_row_delete_field_none_when_can_delete_false(self) -> None:
        formset = _NoDeleteFormset(initial=[{"title": "first"}])
        spec = formset_spec(formset)
        assert spec.rows[0].delete_field is None

    def test_invalid_row_errors_propagate(self) -> None:
        formset = _RowFormset(
            data={
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-title": "",
                "form-0-note": "x",
            }
        )
        assert not formset.is_valid()
        spec = formset_spec(formset)
        assert "title" in spec.rows[0].errors


class _NonFormSurface:
    """Stand-in object exercising the `hasattr(form, 'non_field_errors')` branch."""

    fields: ClassVar[dict[str, object]] = {}


class TestFormSpec:
    """`form_spec` groups fields into sections (with/without fieldsets)."""

    def test_no_fieldsets_wraps_everything_in_single_section(self) -> None:
        spec = form_spec(_SampleForm())
        assert isinstance(spec, FormSpec)
        assert len(spec.sections) == 1
        names = tuple(f.bound.name for f in spec.sections[0].fields)
        assert names == ("name", "email", "notes", "agree", "plan", "tags", "born")

    def test_fieldsets_group_in_order(self) -> None:
        fieldsets = [
            ("Identity", {"fields": ["name", "email"]}),
            ("Other", {"fields": ["notes"], "description": "free text"}),
        ]
        spec = form_spec(_SampleForm(), fieldsets=fieldsets)
        assert [s.label for s in spec.sections] == ["Identity", "Other"]
        assert spec.sections[1].description == "free text"

    def test_nested_field_tuples_flatten_into_one_section(self) -> None:
        fieldsets = [(None, {"fields": [("name", "email")]})]
        spec = form_spec(_SampleForm(), fieldsets=fieldsets)
        assert len(spec.sections) == 1
        assert [f.bound.name for f in spec.sections[0].fields] == ["name", "email"]

    def test_unknown_field_names_in_fieldsets_are_ignored(self) -> None:
        fieldsets = [("Identity", {"fields": ["name", "missing"]})]
        spec = form_spec(_SampleForm(), fieldsets=fieldsets)
        assert [f.bound.name for f in spec.sections[0].fields] == ["name"]

    def test_empty_fieldset_collects_all_fields_into_trailing_section(self) -> None:
        # A label-only fieldset (no `fields`) leaves every form field as
        # leftover and the builder appends one trailing unlabelled section.
        spec = form_spec(_SampleForm(), fieldsets=[("Empty", {})])
        labels = [s.label for s in spec.sections]
        assert labels == ["Empty", ""]
        leftover = spec.sections[1].fields
        assert [f.bound.name for f in leftover] == [
            "name",
            "email",
            "notes",
            "agree",
            "plan",
            "tags",
            "born",
        ]

    def test_non_field_errors_surface(self) -> None:
        global_problem = "global problem"

        class _F(django_forms.Form):
            name = django_forms.CharField()

            def clean(self):
                raise django_forms.ValidationError(global_problem)

        form = _F(data={"name": "ok"})
        form.is_valid()
        spec = form_spec(form)
        assert spec.non_field_errors == (global_problem,)

    def test_form_without_non_field_errors_returns_empty_tuple(self) -> None:
        spec = form_spec(_NonFormSurface())  # type: ignore[arg-type]
        assert spec.non_field_errors == ()


class TestSpecsAreFrozen:
    """All spec dataclasses are `frozen=True, slots=True` and refuse mutation."""

    def test_field_spec_frozen(self) -> None:
        spec = field_spec(_SampleForm()["name"])
        with pytest.raises((AttributeError, TypeError)):
            spec.kind = "checkbox"  # type: ignore[misc]

    def test_form_section_spec_frozen(self) -> None:
        section = FormSectionSpec(label="x", description="", fields=())
        with pytest.raises((AttributeError, TypeError)):
            section.label = "y"  # type: ignore[misc]

    def test_formset_row_spec_frozen(self) -> None:
        row = FormsetRowSpec(
            fields=(),
            hidden_html="",
            delete_field=None,
            errors={},
            is_extra=False,
        )
        with pytest.raises((AttributeError, TypeError)):
            row.is_extra = True  # type: ignore[misc]
