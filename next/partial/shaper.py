"""Implementation of the partial shaper port composed at app startup."""

from typing import TYPE_CHECKING, cast, override

from next.ports import PartialShaper

from .headers import PartialIntent, partial_intent
from .shaping import ActionRef, shape_partial, shape_validate
from .view import zone_response


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse

    from next.forms.backends import FormActionBackend
    from next.forms.dispatch.responses import ActionOutcome
    from next.ports import PartialIntentView


class PartialShaperImpl(PartialShaper):
    """Binds the port to the partial rendering and shaping entry points.

    The port keeps `pages` and `forms` off the `partial` package, so the
    area-owned arguments arrive as `object` or as the narrow intent view
    and are cast back to the types the shaping entry points take.
    """

    @override
    def intent(self, request: "HttpRequest") -> PartialIntent:
        """Return what the request headers ask for."""
        return partial_intent(request)

    @override
    def zone_response(
        self,
        page_path: "Path",
        request: "HttpRequest",
        intent: "PartialIntentView",
        *,
        dynamic_body: bool,
        url_kwargs: dict[str, object],
    ) -> "HttpResponse":
        """Return the envelope for the zones the intent named."""
        return zone_response(
            page_path,
            cast("PartialIntent", intent),
            request,
            dynamic_body=dynamic_body,
            url_kwargs=url_kwargs,
        )

    @override
    def shape_response(
        self, backend: object, request: "HttpRequest", outcome: object
    ) -> "HttpResponse":
        """Return the envelope for one form action outcome."""
        return shape_partial(
            cast("FormActionBackend", backend), request, cast("ActionOutcome", outcome)
        )

    @override
    def shape_validate(
        self,
        backend: object,
        request: "HttpRequest",
        form: "BaseForm | BaseFormSet",
        intent: "PartialIntentView",
        *,
        action_name: str,
        uid: str,
    ) -> "HttpResponse":
        """Return the form morph envelope of a validate-only pass."""
        return shape_validate(
            cast("FormActionBackend", backend),
            request,
            form,
            cast("PartialIntent", intent),
            ActionRef(action_name=action_name, uid=uid),
        )


__all__ = ["PartialShaperImpl"]
