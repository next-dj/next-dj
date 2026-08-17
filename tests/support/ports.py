from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, NoReturn

from next.partial.headers import partial_intent


if TYPE_CHECKING:
    from pathlib import Path

    from django.forms import BaseForm, BaseFormSet
    from django.http import HttpRequest

    from next.partial.headers import PartialIntent


def _shaping_refused(method: str) -> NoReturn:
    """Fail the test because a caller entered a shape method it had to skip."""
    msg = f"{method} was entered for a request whose intent asks for no shaping."
    raise AssertionError(msg)


class IntentOnlyShaper:
    """Shaper stub that answers intent lookups and refuses to shape anything.

    Intent parsing stays real, so a call site keeps taking the same branch it
    takes in production, and reaching a shape method fails the test outright.
    """

    def __init__(self) -> None:
        """Start with every call counter at zero."""
        self.calls: Counter[str] = Counter()

    def intent(self, request: HttpRequest) -> PartialIntent:
        """Count the lookup and return the real intent parsed from the headers."""
        self.calls["intent"] += 1
        return partial_intent(request)

    def zone_response(
        self,
        page_path: Path,
        request: HttpRequest,
        intent: PartialIntent,
        *,
        dynamic_body: bool,
        url_kwargs: dict[str, object],
    ) -> NoReturn:
        """Fail because a request naming no zone must never reach here."""
        del page_path, request, intent, dynamic_body, url_kwargs
        _shaping_refused("zone_response")

    def shape_response(
        self, backend: object, request: HttpRequest, outcome: object
    ) -> NoReturn:
        """Fail because a non-partial outcome must never reach here."""
        del backend, request, outcome
        _shaping_refused("shape_response")

    def shape_validate(
        self,
        backend: object,
        request: HttpRequest,
        form: BaseForm | BaseFormSet,
        intent: PartialIntent,
        *,
        action_name: str,
        uid: str,
    ) -> NoReturn:
        """Fail because a submission naming no validate field must never reach here."""
        del backend, request, form, intent, action_name, uid
        _shaping_refused("shape_validate")
