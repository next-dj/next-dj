from django import forms as django_forms
from django.db.models import F
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect

from next.forms import Form
from next.partial import Patches, is_partial_request
from polls.broker import build_snapshot
from polls.models import Choice, Poll


class VoteForm(Form):
    """Cast a single vote on a choice that belongs to a known poll.

    ``__init__`` narrows the ``choice`` queryset to the submitted poll so a
    forged choice PK fails field validation.
    """

    poll = django_forms.ModelChoiceField(
        queryset=Poll.objects.all(),
        widget=django_forms.HiddenInput,
    )
    choice = django_forms.ModelChoiceField(
        queryset=Choice.objects.none(),
        widget=django_forms.HiddenInput,
    )

    def __init__(self, *args: object, **kwargs: object) -> None:
        """Narrow the choice queryset to the submitted poll on binding."""
        super().__init__(*args, **kwargs)
        poll_pk = self.data.get(self.add_prefix("poll"))
        if poll_pk:
            self.fields["choice"].queryset = Choice.objects.filter(poll_id=poll_pk)

    def on_valid(self, request: HttpRequest) -> HttpResponse:
        """Increment the chosen choice, then morph the zone and push counts.

        A partial vote answers the voter's own tab by morphing the
        `poll-results` zone with the fresh bars and pushing the new
        snapshot into `window.Next.context.live_results`, so the Vue
        island rebinds without waiting for the broker fan-out. Without the
        runtime the vote falls back to a redirect to the poll page.
        """
        selected: Poll = self.cleaned_data["poll"]
        choice: Choice = self.cleaned_data["choice"]
        Choice.objects.filter(pk=choice.pk).update(votes=F("votes") + 1)
        if not is_partial_request(request):
            return HttpResponseRedirect(f"/polls/{selected.pk}/")
        snapshot = build_snapshot(selected).to_payload()
        return (
            Patches(request)
            .morph(zone="poll-results")
            .context(live_results=snapshot)
            .response()
        )
