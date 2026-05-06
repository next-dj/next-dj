from django.db.models import F
from django.http import HttpResponseRedirect
from polls.forms import VoteForm
from polls.models import Choice, Poll
from polls.providers import DPoll

from next.forms import action
from next.pages import context


@context("poll", inherit_context=True)
def poll(active: DPoll[Poll]) -> Poll:
    """Expose the active poll to the layout chain and any nested page."""
    return active


@action("vote", namespace="polls", form_class=VoteForm)
def vote(form: VoteForm) -> HttpResponseRedirect:
    """Atomically increment the chosen choice and redirect to the poll page.

    The `action_dispatched` receiver in `polls.signals` is the single
    publish point for the broker snapshot, so the handler only writes
    to the database. Concurrent voters never lose increments because
    the `F("votes") + 1` expression evaluates atomically inside the
    UPDATE statement.
    """
    selected: Poll = form.cleaned_data["poll"]
    choice: Choice = form.cleaned_data["choice"]
    Choice.objects.filter(pk=choice.pk).update(votes=F("votes") + 1)
    return HttpResponseRedirect(f"/polls/{selected.pk}/")
