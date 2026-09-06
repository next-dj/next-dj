"""Dependency injection markers and provider for form parameters."""

import inspect
from typing import get_args, get_origin, override

from django.forms import BaseForm, BaseFormSet

from next.deps import DDependencyBase, RegisteredParameterProvider, ResolutionContext
from next.deps.markers import unwrap_annotated


# A bound form is a form or a formset, so a plain annotation naming either one
# is a shape the context can carry.
_FORM_BASES: tuple[type, ...] = (BaseForm, BaseFormSet)


class DForm[FormT](DDependencyBase[FormT]):
    """Annotation for injecting a form instance by class.

    The string form spares a page an import it needs for nothing else.
    """

    __slots__ = ()


def _annotated_form_class(annotation: object) -> type | None:
    """Return the form or formset class an annotation names, past any `Annotated`."""
    annotation = unwrap_annotated(annotation)
    if get_origin(annotation) is DForm:
        args = get_args(annotation)
        marked = args[0] if args else None
        return marked if isinstance(marked, type) else None
    if isinstance(annotation, type) and issubclass(annotation, _FORM_BASES):
        return annotation
    return None


class FormProvider(RegisteredParameterProvider):
    """Inject a `form` instance matching the annotation or the parameter name `form`."""

    priority = 40

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when context carries a form compatible with `param`."""
        form = context.form
        if form is None:
            return False
        if param.name == "form":
            return True
        form_class = _annotated_form_class(param.annotation)
        return form_class is not None and isinstance(form, form_class)

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out every annotation no form can inhabit. The rest waits for context.

        Even the `form` name stays open because the context may carry no form.
        The plainest parameters leave here, which keeps the costliest provider out
        of the candidate list of a signature that has nothing to do with forms.
        """
        if param.name == "form":
            return None
        return None if _annotated_form_class(param.annotation) is not None else False

    @override
    def resolve(self, _param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the form instance from context."""
        return context.form


class CleanedDataProvider(RegisteredParameterProvider):
    """Inject merged wizard cleaned data for the parameter named `cleaned_data`."""

    priority = 40

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Return True when context carries cleaned data and the name matches."""
        if param.name != "cleaned_data":
            return False
        return context.cleaned_data is not None

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        """Rule out every other name. A match still needs cleaned data present."""
        return None if param.name == "cleaned_data" else False

    @override
    def resolve(self, _param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the cleaned data mapping from context."""
        return context.cleaned_data


__all__ = ["CleanedDataProvider", "DForm", "FormProvider"]
