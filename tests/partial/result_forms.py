from django import forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

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
