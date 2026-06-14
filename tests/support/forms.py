from typing import Any, ClassVar

from django import forms as django_forms
from django.http import HttpRequest, HttpResponseRedirect, QueryDict

from next.deps import Depends
from next.forms import Form, PermissionOutcome
from next.forms.uid import ORIGIN_FIELD_NAME


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
