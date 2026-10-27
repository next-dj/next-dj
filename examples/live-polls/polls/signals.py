from django import forms as django_forms
from django.conf import settings
from django.dispatch import receiver
from django.http import HttpRequest

from next.forms.signals import action_dispatched
from next.partial import Patches
from next.realtime import publish
from next.static import StaticAsset, StaticCollector
from next.static.signals import collector_finalized
from polls.snapshots import build_snapshot


VOTE_ACTION_NAME = "vote_form"


@receiver(action_dispatched)
def broadcast_vote(
    action_name: str = "",
    form: django_forms.Form | None = None,
    request: HttpRequest | None = None,
    **kwargs,
) -> None:
    """Publish a results-zone refresh after the vote transaction commits."""
    if action_name != VOTE_ACTION_NAME or form is None:
        return
    poll = form.cleaned_data.get("poll")
    if poll is None:
        return
    if request is None:
        return
    snapshot = build_snapshot(poll)
    topic = f"poll:{poll.pk}"
    publish(topic, Patches(request).refresh(zone="poll-results"))
    publish(
        topic,
        Patches(request).event("poll:votes", {"total_votes": snapshot.total_votes}),
    )


def _has_module_assets(collector: StaticCollector) -> bool:
    scripts = collector.assets_in_slot("scripts")
    return any(asset.kind == "vue" for asset in scripts)


def inject_vite_dev_client(sender: StaticCollector, **kwargs) -> None:
    """Prepend the Vite dev client so HMR can attach on pages with Vue assets."""
    if not _has_module_assets(sender):
        return
    origin = settings.VITE_DEV_ORIGIN
    sender.add(StaticAsset(url=f"{origin}/@vite/client", kind="module"), prepend=True)


# Wired only when `VITE_DEV_ORIGIN` opts in, so a plain build needs no dev server.
if settings.VITE_DEV_ORIGIN:
    collector_finalized.connect(inject_vite_dev_client)
