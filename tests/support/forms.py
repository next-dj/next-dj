from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

import pytest
from django import forms as django_forms
from django.core.cache import cache
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, QueryDict

from next.deps import Depends
from next.forms import Form, PermissionOutcome
from next.forms.backends import ActionRegistration
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics
from next.forms.signals import action_dispatched
from next.forms.uid import ORIGIN_FIELD_NAME
from next.forms.wizard import wizard_backend_manager
from next.testing import NextClient, capture_signals
from tests.support.pages import write_page


TWIN_ACTION = "twin"

_TWIN = (
    "from django.http import HttpResponse\n"
    "from next.forms import action\n\n\n"
    '@action("twin")\n'
    "def twin():\n"
    '    return HttpResponse("{label}")\n'
)


def write_twin_pages(root: Path) -> Path:
    """Write routed `create` and `edit` pages that each declare an action named `twin`.

    Each handler answers with the name of its page, so a test sees which one ran.
    """
    for label in ("create", "edit"):
        write_page(root, label, _TWIN.format(label=label), body=label)
    return root


def echo_form(widget: django_forms.Widget) -> type[django_forms.Form]:
    """Build a one-field plain Django form whose field uses `widget`."""

    class _EchoForm(django_forms.Form):
        field = django_forms.CharField(widget=widget, required=True)

    return _EchoForm


def register_page_action(
    name: str, form_class: type[django_forms.Form], file_path: str
) -> None:
    """Register a page-scoped form action through the default backend."""
    form_action_manager.default_backend.register_action(
        ActionRegistration(
            name=name, file_path=file_path, scope="page", form_class=form_class
        )
    )


@contextmanager
def isolated_form_registries() -> Generator[None, None, None]:
    """Snapshot the form registries on entry and put the baseline back on exit.

    Only the manager API moves `version` on restore, not the backend maps by hand.
    """
    actions = form_action_manager.snapshot_actions()
    diagnostics = registration_diagnostics.snapshot()
    wizard_backend_manager.reset()
    cache.clear()
    try:
        yield
    finally:
        form_action_manager.restore_actions(actions)
        registration_diagnostics.restore(diagnostics)
        wizard_backend_manager.reset()
        cache.clear()


def post_without_origin(
    client: NextClient, action: str, data: dict[str, object]
) -> HttpResponse:
    """Post `data` to `action` with no origin field, asserting nothing dispatched."""
    with capture_signals(action_dispatched) as recorder:
        response = client.post(client.get_action_url(action), data)
    assert recorder.events == []
    return response


def logged_request_error(caplog: pytest.LogCaptureFixture) -> BaseException | None:
    """Return the exception of the one `django.request` record `caplog` holds."""
    [record] = [r for r in caplog.records if r.name == "django.request"]
    assert record.exc_info is not None
    return record.exc_info[1]


def build_post_request(mock_http_request, *, origin: str = "/") -> HttpRequest:
    """Return a POST request carrying a name field and a validated origin."""
    post = QueryDict(mutable=True)
    post["name"] = "Ada"
    post[ORIGIN_FIELD_NAME] = origin
    return mock_http_request(method="POST", POST=post, FILES=None)


class GuardedTenantForm(Form):
    """A view hook plus get_initial and on_valid share one Depends provider."""

    name = django_forms.CharField(max_length=50)
    resolutions: ClassVar[list[str]] = []

    @classmethod
    def get_initial(cls, tenant: str = Depends("tenant")) -> dict[str, Any]:
        """Read the shared provider during initial resolution."""
        assert tenant == "acme"
        return {}

    @classmethod
    def check_permissions(
        cls, request: HttpRequest, tenant: str = Depends("tenant")
    ) -> PermissionOutcome:
        """Read the shared provider during the view hook."""
        assert tenant == "acme"
        return None

    def on_valid(
        self, request: HttpRequest, tenant: str = Depends("tenant")
    ) -> HttpResponseRedirect:
        """Read the shared provider during finalisation."""
        assert tenant == "acme"
        return HttpResponseRedirect("/")
