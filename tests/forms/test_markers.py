import pytest
from django import forms as django_forms

from next.forms import Form
from next.forms.markers import CleanedDataProvider, DForm, FormProvider
from next.testing import make_resolution_context
from tests.support import inspect_parameter


class _RowForm(Form):
    name = django_forms.CharField()


class _OtherForm(Form):
    email = django_forms.EmailField()


_RowFormset = django_forms.formset_factory(_RowForm, extra=1)


class TestDFormAndFormProvider:
    """DForm marker and FormProvider DI provider."""

    def test_dform_can_be_instantiated(self) -> None:
        """DForm[Form] annotation can be created."""
        assert DForm[Form] is not None

    @pytest.mark.parametrize(
        ("param", "expected"),
        [
            pytest.param(inspect_parameter("form"), True, id="named-form"),
            pytest.param(
                inspect_parameter("my_form", _RowForm), True, id="matching-annotation"
            ),
            pytest.param(
                inspect_parameter("typed_form", DForm[_RowForm]), True, id="dform"
            ),
            pytest.param(
                inspect_parameter("typed_form", DForm[_OtherForm]),
                False,
                id="dform-other-type",
            ),
            pytest.param(inspect_parameter("other_param"), False, id="unrelated"),
        ],
    )
    def test_can_handle_with_a_form_in_context(self, param, expected) -> None:
        """FormProvider.can_handle matches by name or by the form type."""
        context = make_resolution_context(form=_RowForm())
        assert FormProvider().can_handle(param, context) is expected

    def test_can_handle_is_false_when_no_form(self) -> None:
        """FormProvider.can_handle returns False when context has no form."""
        context = make_resolution_context()
        assert FormProvider().can_handle(inspect_parameter("form"), context) is False

    def test_resolve_returns_the_form_from_context(self) -> None:
        """FormProvider.resolve returns the form instance from context."""
        form_instance = _RowForm()
        context = make_resolution_context(form=form_instance)
        result = FormProvider().resolve(inspect_parameter("form"), context)
        assert result is form_instance

    def test_form_provider_handles_formset_class_annotation(self) -> None:
        """A bound formset fills a parameter annotated with its own class."""
        provider = FormProvider()
        formset = _RowFormset()
        context = make_resolution_context(form=formset)
        param = inspect_parameter("rows", _RowFormset)

        assert provider.static_can_handle(param) is None
        assert provider.can_handle(param, context) is True
        assert provider.resolve(param, context) is formset

    def test_form_provider_rules_out_a_class_no_form_inhabits(self) -> None:
        """A plain class that is neither a form nor a formset leaves the plan."""
        param = inspect_parameter("service", int)
        assert FormProvider().static_can_handle(param) is False


class TestCleanedDataProvider:
    """CleanedDataProvider claims only the `cleaned_data` parameter."""

    @pytest.mark.parametrize(
        ("name", "context_data", "expected"),
        [
            pytest.param("cleaned_data", {"name": "Ada"}, True, id="named-with-data"),
            pytest.param("payload", {"name": "Ada"}, False, id="other-name"),
            pytest.param("cleaned_data", None, False, id="missing-data"),
        ],
    )
    def test_can_handle(self, name: str, context_data, expected) -> None:
        """can_handle needs the `cleaned_data` name and data on the context."""
        context = make_resolution_context(cleaned_data=context_data)
        assert CleanedDataProvider().can_handle(inspect_parameter(name), context) is (
            expected
        )

    def test_resolve_returns_mapping_from_context(self) -> None:
        """CleanedDataProvider.resolve returns the mapping stored on the context."""
        context = make_resolution_context(cleaned_data={"name": "Ada"})
        param = inspect_parameter("cleaned_data")
        assert CleanedDataProvider().resolve(param, context) == {"name": "Ada"}
