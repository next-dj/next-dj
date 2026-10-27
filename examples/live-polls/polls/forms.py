from django import forms as django_forms
from django.db.models import F
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.partial import Patches, is_partial_request
from next.urls import page_reverse
from polls.models import Choice, Poll
from polls.snapshots import build_snapshot


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

    def clean(self) -> dict[str, object]:
        """Reject votes against a poll that has closed or reached its deadline."""
        cleaned_data = super().clean()
        poll = cleaned_data.get("poll")
        if poll is not None and not poll.accepts_votes():
            self.add_error(None, "This poll is closed.")
        return cleaned_data

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Increment the chosen choice, then morph the results zone with new counts."""
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
