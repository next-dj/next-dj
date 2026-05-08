"""Signal wiring for the polls app.

Two listeners live here. The first broadcasts a fresh snapshot to the
in-process broker after every successful vote. It is enabled by the
`form` and `url_kwargs` fields the framework added to
`action_dispatched` so the receiver knows which poll changed without
re-querying state. The second injects the Vite dev client into pages
that carry Vue assets when DEBUG is true so HMR works during local
development.
"""

from django import forms as django_forms
from django.conf import settings
from django.dispatch import receiver

from next.forms.signals import action_dispatched
from next.static.assets import StaticAsset
from next.static.signals import collector_finalized
from polls.broker import broker, build_snapshot


VOTE_ACTION_NAME = "polls:vote"


@receiver(action_dispatched)
def broadcast_vote(
    action_name: str = "",
    form: django_forms.Form | None = None,
    **_: object,
) -> None:
    """Publish a fresh snapshot for the poll that just received a vote.

    The receiver consumes the bound form that the framework attaches
    to `action_dispatched`. Without that field the receiver could not
    tell which poll changed without reissuing the query. Other
    payload fields (`sender`, `url_kwargs`, `duration_ms`,
    `response_status`) are absorbed by `**_` because this listener
    only needs the form.
    """
    if action_name != VOTE_ACTION_NAME or form is None:
        return
    poll = form.cleaned_data.get("poll")
    if poll is None:
        return
    snapshot = build_snapshot(poll)
    broker.publish(snapshot)


def _has_module_assets(collector: object) -> bool:
    scripts = collector.assets_in_slot("scripts")  # type: ignore[attr-defined]
    return any(asset.kind == "vue" for asset in scripts)


def inject_vite_dev_client(sender: object, **_kwargs: object) -> None:
    """Prepend the Vite dev client so HMR can attach on pages with Vue assets.

    Vue does not need a React Refresh preamble. The single
    `@vite/client` module script is enough for HMR through
    `@vitejs/plugin-vue`. The receiver fires only on pages that
    already carry `vue` scripts so plain Django pages stay free of
    dev plumbing.
    """
    if not _has_module_assets(sender):
        return
    origin = settings.VITE_DEV_ORIGIN
    sender.add(  # type: ignore[attr-defined]
        StaticAsset(url=f"{origin}/@vite/client", kind="module"),
        prepend=True,
    )


# Wire the dev-client injector only when the developer opted into the
# Vite dev server through `VITE_DEV_ORIGIN`. Without that env var the
# example loads bundled assets from the manifest and never tries to
# reach a dev server, so opening the page after `npm run build` works
# in a single terminal.
if settings.VITE_DEV_ORIGIN:
    collector_finalized.connect(inject_vite_dev_client)
