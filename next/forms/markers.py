"""Dependency injection markers and provider for form parameters."""

import inspect
from typing import get_args, get_origin, override

from next.deps import (
    DDependencyBase,
    RegisteredParameterProvider,
    ResolutionContext,
)


class DForm[FormT](DDependencyBase[FormT]):
    r"""Annotation for injecting a form instance by class.

    Use as `DForm[MyForm]` or `DForm["MyForm"]`.
    """

    __slots__ = ()


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
        ann = param.annotation
        if ann is inspect.Parameter.empty:
            return False
        origin = get_origin(ann)
        if origin is DForm:
            args = get_args(ann)
            if len(args) >= 1:
                form_class = args[0]
                if isinstance(form_class, type) and isinstance(form, form_class):
                    return True
            return False
        return isinstance(ann, type) and isinstance(form, ann)

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
    def resolve(self, _param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the cleaned data mapping from context."""
        return context.cleaned_data


__all__ = ["CleanedDataProvider", "DForm", "FormProvider"]
