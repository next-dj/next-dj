"""The default response shaper, answering partial form requests with patch envelopes."""

from typing import TYPE_CHECKING, override

from next.forms.backends import ResponseShaper
from next.partial.headers import partial_intent

from .outcomes import shape_partial
from .validate import ActionRef, shape_validate


if TYPE_CHECKING:
    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest, HttpResponse

    from next.forms.backends import FormActionBackend
    from next.forms.dispatch.responses import ActionOutcome
    from next.forms.wizard import FormWizard


class FormShaper(ResponseShaper):
    """Shapes a partial form request into a patch envelope, a plain one never."""

    @override
    def shape(
        self,
        backend: "FormActionBackend",
        request: "HttpRequest",
        outcome: "ActionOutcome",
    ) -> "HttpResponse | None":
        """Return the envelope for one outcome of a partial request."""
        if not partial_intent(request).partial:
            return None
        return shape_partial(backend, request, outcome)

    @override
    def validate(
        self,
        backend: "FormActionBackend",
        request: "HttpRequest",
        form: "BaseForm | BaseFormSet",
        *,
        action_name: str,
        uid: str,
        wizard: "FormWizard | None",
    ) -> "HttpResponse | None":
        """Return the form morph envelope when the request names fields to validate."""
        intent = partial_intent(request)
        if not intent.validate_fields:
            return None
        return shape_validate(
            backend,
            request,
            form,
            intent,
            ActionRef(action_name=action_name, uid=uid),
            wizard=wizard,
        )


__all__ = ["FormShaper"]
