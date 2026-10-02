from dataclasses import dataclass

from django import forms as django_forms
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect
from django.http.request import QueryDict
from django.urls import reverse

from next.forms import Form
from next.partial import Patches, is_partial_request

from .zones import LISTING_ZONES


@dataclass(frozen=True, slots=True)
class Preset:
    """A canonical listing querystring and the tab title it applies."""

    title: str
    params: dict[str, str]


PRESETS: dict[str, Preset] = {
    "in_stock": Preset("In stock", {"in_stock": "1"}),
    "cheapest": Preset("Cheapest first", {"sort": "price_asc"}),
    "newest": Preset("Newest", {"sort": "newest"}),
}


class PresetFilterForm(Form):
    """Apply a named preset filter to the all-products listing.

    A preset is a deliberate choice, so unlike the live filter it earns a history entry.
    """

    preset = django_forms.ChoiceField(
        choices=[(name, name) for name in PRESETS], widget=django_forms.HiddenInput
    )

    def _target(self) -> str:
        """Return the canonical listing URL the chosen preset maps to."""
        params = PRESETS[self.cleaned_data["preset"]].params
        base = reverse("next:page_catalog")
        query = QueryDict(mutable=True)
        query.update(params)
        encoded = query.urlencode()
        return f"{base}?{encoded}" if encoded else base

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Push the canonical preset URL and morph the listing zones under it.

        Pointing `request.GET` at the preset renders the zones as a navigation would.
        """
        preset = PRESETS[self.cleaned_data["preset"]]
        target = self._target()
        if not is_partial_request(request):
            return HttpResponseRedirect(target)
        request.GET = QueryDict(mutable=True)
        request.GET.update(preset.params)
        patches = Patches(request).push_url(target).meta(preset.title)
        for zone in LISTING_ZONES:
            patches.morph(zone=zone)
        return patches.response()
