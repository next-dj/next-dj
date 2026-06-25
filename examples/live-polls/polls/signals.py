from django import forms as django_forms
from django.conf import settings
from django.dispatch import receiver
from django.http import HttpRequest

from next.forms.signals import action_dispatched
from next.partial.headers import REQUEST_ID
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.static.signals import collector_finalized
from polls.broker import broker, build_snapshot


VOTE_ACTION_NAME = "vote_form"


@receiver(action_dispatched)
def broadcast_vote(
    action_name: str = "",
    form: django_forms.Form | None = None,
    request: HttpRequest | None = None,
    **_: object,
) -> None:
    """Publish a fresh snapshot for the poll that just received a vote.

    The receiver consumes the bound form the framework attaches to
    `action_dispatched` to tell which poll changed without reissuing the
    query, and the request to read the mutation's `X-Next-Request-Id`.
    Threading that id to `broker.publish` lets the stream echo it so the
    voter's own tab drops the fan-out update. Other payload fields are
    absorbed by `**_` because this listener needs only the form and the
    request.
    """
    if action_name != VOTE_ACTION_NAME or form is None:
        return
    poll = form.cleaned_data.get("poll")
    if poll is None:
        return
    request_id = request.headers.get(REQUEST_ID) if request is not None else None
    snapshot = build_snapshot(poll)
    broker.publish(snapshot, request_id=request_id)


def _has_module_assets(collector: StaticCollector) -> bool:
    scripts = collector.assets_in_slot("scripts")
    return any(asset.kind == "vue" for asset in scripts)


def inject_vite_dev_client(sender: StaticCollector, **_kwargs: object) -> None:
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
    sender.add(
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
