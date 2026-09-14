from django import forms as django_forms
from django.db.models import F
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.partial import Patches, is_partial_request
from next.urls import page_reverse
from polls.broker import build_snapshot
from polls.models import Choice, Poll


class VoteForm(Form):
    """Cast a single vote on a choice that belongs to a known poll.

    Narrowing `choice` to the submitted poll makes a forged choice PK fail validation.
    """

    poll = django_forms.ModelChoiceField(
        queryset=Poll.objects.all(), widget=django_forms.HiddenInput
    )
    choice = django_forms.ModelChoiceField(
        queryset=Choice.objects.none(), widget=django_forms.HiddenInput
    )

    def __init__(self, *args, **kwargs) -> None:
        """Narrow the choice queryset to the submitted poll on binding."""
        super().__init__(*args, **kwargs)
        poll_pk = self.data.get(self.add_prefix("poll"))
        if poll_pk:
            self.fields["choice"].queryset = Choice.objects.filter(poll_id=poll_pk)

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Increment the chosen choice, then morph the zone and push counts.

        A partial vote morphs `poll-results` and pushes the snapshot into
        `live_results`, so the Vue island rebinds ahead of the fan-out.
        """
        selected: Poll = self.cleaned_data["poll"]
        choice: Choice = self.cleaned_data["choice"]
        Choice.objects.filter(pk=choice.pk).update(votes=F("votes") + 1)
        if not is_partial_request(request):
            return HttpResponseRedirect(page_reverse("polls/[int:id]", id=selected.pk))
        snapshot = build_snapshot(selected).to_payload()
        return (
            Patches(request)
            .morph(zone="poll-results")
            .context(live_results=snapshot)
            .response()
        )
