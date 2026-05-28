from django import forms as django_forms
from django.db.models import F
from django.http import HttpRequest, HttpResponseRedirect

from next.forms import Form
from polls.models import Choice, Poll


class VoteForm(Form):
    """Cast a single vote on a choice that belongs to a known poll.

    The form binds the chosen ``Choice`` and its parent ``Poll`` so the
    handler and the ``action_dispatched`` receiver can read both without
    a second query.

    On binding, ``__init__`` narrows the ``choice`` queryset to only the
    choices of the submitted poll so Django's own ``ModelChoiceField``
    rejects forged PKs at field-validation time — no extra ``clean``
    cross-check is required.
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

    def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
        """Atomically increment the chosen choice and redirect to the poll page.

        The `action_dispatched` receiver in `polls.signals` is the single
        publish point for the broker snapshot, so the handler only writes
        to the database. Concurrent voters never lose increments because
        the `F("votes") + 1` expression evaluates atomically inside the
        UPDATE statement.
        """
        selected: Poll = self.cleaned_data["poll"]
        choice: Choice = self.cleaned_data["choice"]
        Choice.objects.filter(pk=choice.pk).update(votes=F("votes") + 1)
        return HttpResponseRedirect(f"/polls/{selected.pk}/")
