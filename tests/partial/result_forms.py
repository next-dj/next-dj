from typing import Any

from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.middleware.csrf import rotate_token

from next.forms import Form
from next.partial import Patches, PatchResponse


class InternalRedirectForm(Form):
    """Form whose handler redirects to a same-site URL.

    The shaping layer packs the redirect into an internal visit the host
    validator approves.
    """

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect to a same-site path."""
        return HttpResponseRedirect("/done/")


class ExternalRedirectForm(Form):
    """Form whose handler redirects to an external host, the OAuth path.

    The shaping layer must pack the external URL into a visit carrying the
    full-navigation marker so the same-host validator does not reject it.
    """

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Redirect to an external host such as an identity provider."""
        return HttpResponseRedirect("https://oauth.example.com/authorize")


class AuthoredPatchForm(Form):
    """Form whose handler authors its own patch envelope.

    A handler that returns a PatchResponse passes through unchanged.
    """

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> PatchResponse:
        """Return a hand-built envelope that the shaping layer passes through."""
        return Patches(request).toast("authored", variant="success").response()


class RichResponseForm(Form):
    """Form whose handler returns a plain HttpResponse, not a redirect or patch.

    A non-redirect rich response falls through to the existing full path.
    """

    name = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Return a plain HTML response the default shaping path serves."""
        return HttpResponse("<p>plain</p>")


class RotatingResultForm(Form):
    """Form that rotates the CSRF token then succeeds with a None result.

    A login mid-submit rotates the token, so this proves the success
    funnel envelope carries the fresh CSRF payload, not only the validate
    path. The rotation runs during initial resolution, before any zone or
    form re-render mints a token.
    """

    name = forms.CharField(max_length=100)

    @classmethod
    def get_initial(cls, request: HttpRequest) -> dict[str, Any]:
        """Rotate the request CSRF token before the form binds."""
        rotate_token(request)
        return {}

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the success funnel."""
        return None


class RotatingInvalidForm(Form):
    """Form that rotates the CSRF token then fails validation.

    The invalid-shape envelope must carry the fresh CSRF payload so a
    rotation on a failed submit still refreshes the document tokens.
    """

    name = forms.CharField(max_length=100)

    @classmethod
    def get_initial(cls, request: HttpRequest) -> dict[str, Any]:
        """Rotate the request CSRF token before the form binds."""
        rotate_token(request)
        return {}

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the submission and fall back to the default redirect."""
        return None
