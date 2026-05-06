from django import forms as django_forms

from next.forms import Form
from polls.models import Choice, Poll


class VoteForm(Form):
    """Cast a single vote on a choice that belongs to a known poll.

    The form binds the chosen `Choice` and its parent `Poll` so the
    handler and the action_dispatched receiver can read both without a
    second query. The cross-poll check rejects requests where a forged
    choice id points at a row that does not belong to the submitted
    poll.
    """

    poll = django_forms.ModelChoiceField(
        queryset=Poll.objects.all(),
        widget=django_forms.HiddenInput,
    )
    choice = django_forms.ModelChoiceField(
        queryset=Choice.objects.all(),
        widget=django_forms.HiddenInput,
    )

    def clean(self) -> dict[str, object]:
        """Reject choices that do not belong to the submitted poll."""
        cleaned = super().clean() or {}
        poll = cleaned.get("poll")
        choice = cleaned.get("choice")
        if poll is None or choice is None:
            return cleaned
        if choice.poll_id != poll.pk:
            msg = "Choice does not belong to this poll."
            raise django_forms.ValidationError(msg)
        return cleaned
