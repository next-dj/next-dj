"""Dependency injection markers and provider for form parameters."""

from __future__ import annotations

import inspect
from typing import get_args, get_origin

from next.deps import DDependencyBase, RegisteredParameterProvider


class DForm[FormT](DDependencyBase[FormT]):
    r"""Annotation for injecting a form instance by class.

    Use as `DForm[MyForm]` or `DForm["MyForm"]`.
    """

    __slots__ = ()


class FormProvider(RegisteredParameterProvider):
    """Inject a `form` instance matching the annotation or the parameter name `form`."""

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        """Return True when context carries a form compatible with `param`."""
        form = getattr(context, "form", None)
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

    def resolve(self, _param: inspect.Parameter, context: object) -> object:
        """Return the form instance from context."""
        return getattr(context, "form", None)


__all__ = ["DForm", "FormProvider"]
