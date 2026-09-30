"""Dependency injection of the `Consent` a request carries."""

import inspect
from typing import override

from next.deps import RegisteredParameterProvider
from next.deps.context import ResolutionContext
from next.pages.responses import vary_on_cookie

from .manager import get_consent, server_mode
from .markers import UNDECIDED, Consent


class ConsentProvider(RegisteredParameterProvider):
    """Supply the `Consent` of the request to a parameter annotated with it.

    A render the server keeps off the cookie sees an undecided visitor, the rest vary.
    """

    priority = 50

    @override
    def can_handle(self, param: inspect.Parameter, context: ResolutionContext) -> bool:
        """Defer to the static verdict, which the context never changes."""
        return self.static_can_handle(param) is True

    @override
    def static_can_handle(self, param: inspect.Parameter) -> bool:
        """Settle on the annotation alone."""
        return param.annotation is Consent

    @override
    def resolve(self, param: inspect.Parameter, context: ResolutionContext) -> object:
        """Return the consent of the request, undecided where the server reads none."""
        request = context.request
        if not server_mode(request):
            return UNDECIDED
        vary_on_cookie(request)
        return get_consent(request)


__all__ = ["ConsentProvider"]
