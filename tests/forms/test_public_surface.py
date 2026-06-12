import pytest
from django import forms as django_forms

from next import forms as next_forms


class TestDjangoMirrorFallback:
    """Module __getattr__ resolves public django.forms names."""

    def test_fallback_resolves_django_only_names(self) -> None:
        assert next_forms.modelform_factory is django_forms.modelform_factory
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
