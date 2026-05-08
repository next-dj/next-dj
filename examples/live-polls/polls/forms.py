from django import forms as django_forms

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
