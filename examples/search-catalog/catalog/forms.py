from django import forms as django_forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.request import QueryDict
from django.urls import reverse

from next.forms import Form
from next.partial import Patches, is_partial_request


RESULT_ZONES = ("catalog-results", "catalog-more")

# Each preset is a canonical querystring the storefront could also reach by
# a plain link. Applying one is a discrete jump, unlike the debounced live
# filter, so it earns a history entry through push_url.
PRESETS: dict[str, dict[str, str]] = {
    "in_stock": {"in_stock": "1"},
    "cheapest": {"sort": "price_asc"},
    "newest": {"sort": "newest"},
}


class PresetFilterForm(Form):
    """Apply a named preset filter to the all-products listing.

    A preset is a deliberate choice that should sit in browser history, so
    a partial apply morphs the result zones and pushes the canonical URL.
    Without the runtime the apply falls back to a plain navigation.
    """

    preset = django_forms.ChoiceField(
        choices=[(name, name) for name in PRESETS],
        widget=django_forms.HiddenInput,
    )

    def _target(self) -> str:
        """Return the canonical listing URL the chosen preset maps to."""
        params = PRESETS[self.cleaned_data["preset"]]
        base = reverse("next:page_catalog")
        query = QueryDict(mutable=True)
        query.update(params)
        encoded = query.urlencode()
        return f"{base}?{encoded}" if encoded else base

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Push the canonical preset URL and morph the listing under it.

        Pointing `request.GET` at the preset's querystring makes the zones
        re-render exactly as a navigation to that URL would, so the cached
        search, the active-filter chips, and the pagination sentinel all
        agree with the URL push_url writes to history.
        """
        params = PRESETS[self.cleaned_data["preset"]]
        target = self._target()
        if not is_partial_request(request):
            return HttpResponseRedirect(target)
        request.GET = QueryDict(mutable=True)
        request.GET.update(params)
        patches = Patches(request).push_url(target)
        for zone in RESULT_ZONES:
            patches.morph(zone=zone)
        return patches.response()
