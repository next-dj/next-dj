import pytest
from django import forms as django_forms
from django.forms import formsets as django_formsets, widgets as django_widgets

from next import forms as next_forms
from next.forms import formsets as next_formsets, widgets as next_widgets


class TestDjangoMirrorFallback:
    """Module __getattr__ resolves public django.forms names."""

    def test_fallback_resolves_django_only_names(self) -> None:
        assert next_forms.SplitDateTimeWidget is django_forms.SplitDateTimeWidget
        assert next_forms.BaseFormSet is django_forms.BaseFormSet
        assert next_forms.SplitDateTimeField is django_forms.SplitDateTimeField

    def test_static_block_covers_modelform_vocabulary(self) -> None:
        assert next_forms.SlugField is django_forms.SlugField
        assert next_forms.UUIDField is django_forms.UUIDField
        assert next_forms.JSONField is django_forms.JSONField
        assert next_forms.DurationField is django_forms.DurationField
        assert next_forms.ModelChoiceField is django_forms.ModelChoiceField
        assert (
            next_forms.ModelMultipleChoiceField is django_forms.ModelMultipleChoiceField
        )
        assert next_forms.FileInput is django_forms.FileInput
        assert next_forms.ClearableFileInput is django_forms.ClearableFileInput
        assert next_forms.RadioSelect is django_forms.RadioSelect
        assert next_forms.CheckboxSelectMultiple is django_forms.CheckboxSelectMultiple

    def test_next_forms_overrides_shadow_django(self) -> None:
        assert next_forms.Form is not django_forms.Form
        assert next_forms.ModelForm is not django_forms.ModelForm
        assert next_forms.BaseForm is not django_forms.BaseForm
        assert next_forms.BaseModelForm is not django_forms.BaseModelForm

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'NotAFormThing'"):
            next_forms.__getattr__("NotAFormThing")

    def test_private_names_never_fall_back(self) -> None:
        with pytest.raises(AttributeError, match="_not_a_real_helper"):
            next_forms.__getattr__("_not_a_real_helper")

    def test_hasattr_contract_stays_honest(self) -> None:
        assert hasattr(next_forms, "modelform_factory")
        assert not hasattr(next_forms, "NotAFormThing")


class TestStaticPassthroughExports:
    """High-traffic factory names are statically re-exported for type checkers."""

    @pytest.mark.parametrize(
        "name",
        [
            "BoundField",
            "formset_factory",
            "inlineformset_factory",
            "modelform_factory",
            "modelformset_factory",
        ],
    )
    def test_name_is_exported_and_identical_to_django(self, name: str) -> None:
        assert name in next_forms.__all__
        assert getattr(next_forms, name) is getattr(django_forms, name)


class TestDirContract:
    """Module __dir__ lists the curated surface plus the django.forms namespace."""

    def test_dir_is_superset_of_all(self) -> None:
        listed = dir(next_forms)
        assert set(next_forms.__all__) <= set(listed)

    def test_dir_includes_public_django_names(self) -> None:
        listed = set(dir(next_forms))
        django_public = {n for n in dir(django_forms) if not n.startswith("_")}
        assert django_public <= listed

    def test_dir_is_sorted_and_underscore_free(self) -> None:
        listed = dir(next_forms)
        assert listed == sorted(listed)
        assert not any(name.startswith("_") for name in listed)


class TestWidgetsModuleMirror:
    """next.forms.widgets falls back to public django.forms.widgets names."""

    def test_resolves_django_widget_names(self) -> None:
        assert next_widgets.TextInput is django_widgets.TextInput
        assert next_widgets.Select is django_widgets.Select
        assert next_widgets.ClearableFileInput is django_widgets.ClearableFileInput

    def test_own_names_stay_first(self) -> None:
        assert next_widgets.ComponentWidget.__module__ == "next.forms.widgets"
        assert callable(next_widgets.bind_component_widgets)

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'NotAWidget'"):
            next_widgets.__getattr__("NotAWidget")

    def test_private_names_never_fall_back(self) -> None:
        with pytest.raises(AttributeError, match="_not_a_real_widget"):
            next_widgets.__getattr__("_not_a_real_widget")

    def test_dir_lists_django_and_curated_names(self) -> None:
        listed = set(dir(next_widgets))
        django_public = {n for n in dir(django_widgets) if not n.startswith("_")}
        assert django_public <= listed
        assert "ComponentWidget" in listed


class TestFormsetsModuleMirror:
    """next.forms.formsets falls back to public django.forms.formsets names."""

    def test_resolves_django_formset_names(self) -> None:
        assert next_formsets.formset_factory is django_formsets.formset_factory
        assert next_formsets.BaseFormSet is django_formsets.BaseFormSet
        assert next_formsets.all_valid is django_formsets.all_valid

    def test_own_names_stay_first(self) -> None:
        assert callable(next_formsets.cleanup_extra_initial)
        assert next_formsets.cleanup_extra_initial.__module__ == "next.forms.formsets"

    def test_unknown_name_raises_attribute_error(self) -> None:
        with pytest.raises(AttributeError, match="no attribute 'NotAFormSetThing'"):
            next_formsets.__getattr__("NotAFormSetThing")

    def test_private_names_never_fall_back(self) -> None:
        with pytest.raises(AttributeError, match="_not_a_real_formset"):
            next_formsets.__getattr__("_not_a_real_formset")

    def test_dir_lists_django_and_curated_names(self) -> None:
        listed = set(dir(next_formsets))
        django_public = {n for n in dir(django_formsets) if not n.startswith("_")}
        assert django_public <= listed
        assert "cleanup_extra_initial" in listed
