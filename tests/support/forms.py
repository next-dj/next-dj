from collections.abc import Generator
from contextlib import contextmanager
from typing import Any, ClassVar

from django import forms as django_forms
from django.core.cache import cache
from django.http import HttpRequest, HttpResponseRedirect, QueryDict

from next.deps import Depends
from next.forms import Form, PermissionOutcome
from next.forms.backends import ActionRegistration
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics
from next.forms.uid import ORIGIN_FIELD_NAME
from next.forms.wizard import wizard_backend_manager


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
