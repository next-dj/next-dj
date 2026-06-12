import inspect
from unittest.mock import MagicMock

from django import forms as django_forms

from next.forms import Form
from next.forms.markers import CleanedDataProvider, DForm, FormProvider


class TestDFormAndFormProvider:
    """DForm marker and FormProvider DI provider."""

    def test_dform_can_be_instantiated(self) -> None:
        """DForm[Form] annotation can be created."""
        ann = DForm[Form]
        assert ann is not None

    def test_form_provider_handles_param_named_form(self) -> None:
        """FormProvider.can_handle returns True for 'form' param with a form in context."""
        provider = FormProvider()

        class MyForm(Form):
            name = django_forms.CharField()

        context = MagicMock()
        context.form = MyForm()

        param = inspect.Parameter("form", inspect.Parameter.POSITIONAL_OR_KEYWORD)
        assert provider.can_handle(param, context) is True

    def test_form_provider_handles_annotated_param(self) -> None:
        """FormProvider.can_handle returns True when annotation matches form type."""
        provider = FormProvider()

        class MyForm(Form):
            name = django_forms.CharField()

        context = MagicMock()
        context.form = MyForm()

        param = inspect.Parameter(
            "my_form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=MyForm,
        )
        assert provider.can_handle(param, context) is True

    def test_form_provider_returns_false_when_no_form(self) -> None:
        """FormProvider.can_handle returns False when context has no form."""
        provider = FormProvider()
        context = MagicMock()
        context.form = None

        param = inspect.Parameter("form", inspect.Parameter.POSITIONAL_OR_KEYWORD)
        assert provider.can_handle(param, context) is False

    def test_form_provider_returns_false_for_empty_annotation(self) -> None:
        """FormProvider.can_handle returns False when annotation is empty."""
        provider = FormProvider()

        class MyForm(Form):
            name = django_forms.CharField()

        context = MagicMock()
        context.form = MyForm()

        param = inspect.Parameter(
            "other_param",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        assert provider.can_handle(param, context) is False

    def test_form_provider_resolves_form_from_context(self) -> None:
        """FormProvider.resolve returns the form instance from context."""
        provider = FormProvider()

        class MyForm(Form):
            name = django_forms.CharField()

        form_instance = MyForm()
        context = MagicMock()
        context.form = form_instance

        param = inspect.Parameter("form", inspect.Parameter.POSITIONAL_OR_KEYWORD)
        result = provider.resolve(param, context)
        assert result is form_instance

    def test_form_provider_handles_dform_annotation(self) -> None:
        """FormProvider.can_handle returns True for DForm[MyForm] annotation."""
        provider = FormProvider()

        class MyForm(Form):
            name = django_forms.CharField()

        context = MagicMock()
        context.form = MyForm()

        param = inspect.Parameter(
            "typed_form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DForm[MyForm],
        )
        assert provider.can_handle(param, context) is True

    def test_form_provider_dform_wrong_type_returns_false(self) -> None:
        """FormProvider.can_handle returns False when DForm type doesn't match form instance."""
        provider = FormProvider()

        class FormA(Form):
            name = django_forms.CharField()

        class FormB(Form):
            email = django_forms.EmailField()

        context = MagicMock()
        context.form = FormA()

        param = inspect.Parameter(
            "typed_form",
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            annotation=DForm[FormB],
        )
        assert provider.can_handle(param, context) is False


class TestCleanedDataProvider:
    """CleanedDataProvider claims only the `cleaned_data` parameter."""

    def test_can_handle_named_param_with_data(self) -> None:
        """can_handle returns True for `cleaned_data` when context carries data."""
        provider = CleanedDataProvider()
        context = MagicMock()
        context.cleaned_data = {"name": "Ada"}
        param = inspect.Parameter(
            "cleaned_data", inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        assert provider.can_handle(param, context) is True

    def test_can_handle_rejects_other_names(self) -> None:
        """can_handle returns False for any other parameter name."""
        provider = CleanedDataProvider()
        context = MagicMock()
        context.cleaned_data = {"name": "Ada"}
        param = inspect.Parameter("payload", inspect.Parameter.POSITIONAL_OR_KEYWORD)
        assert provider.can_handle(param, context) is False

    def test_can_handle_rejects_missing_data(self) -> None:
        """can_handle returns False when the context carries no cleaned data."""
        provider = CleanedDataProvider()
        context = MagicMock()
        context.cleaned_data = None
        param = inspect.Parameter(
            "cleaned_data", inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        assert provider.can_handle(param, context) is False

    def test_resolve_returns_mapping_from_context(self) -> None:
        """CleanedDataProvider.resolve returns the mapping stored on the context."""
        provider = CleanedDataProvider()
        context = MagicMock()
        context.cleaned_data = {"name": "Ada"}
        param = inspect.Parameter(
            "cleaned_data", inspect.Parameter.POSITIONAL_OR_KEYWORD
        )
        assert provider.resolve(param, context) == {"name": "Ada"}
