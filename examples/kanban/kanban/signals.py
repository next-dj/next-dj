import base64

from django import forms as django_forms
from django.conf import settings
from django.dispatch import receiver
from django.http import HttpRequest

from kanban.realtime import board_topic
from next.conf import next_framework_settings
from next.forms.signals import action_dispatched
from next.partial import Patches
from next.realtime import publish
from next.static import StaticAsset
from next.static.signals import collector_finalized


BOARD_ACTIONS = {"move_card_form": "target_column", "create_card_form": "column"}
HTTP_ERROR_FLOOR = 400


@receiver(action_dispatched)
def broadcast_board_change(
    action_name: str = "",
    form: django_forms.Form | None = None,
    request: HttpRequest | None = None,
    response_status: int = 200,
    **kwargs,
) -> None:
    """Ask every other tab of the board to refresh it once the change commits."""
    field = BOARD_ACTIONS.get(action_name)
    if field is None or form is None or request is None:
        return
    if response_status >= HTTP_ERROR_FLOOR:
        return
    column = form.cleaned_data[field]
    publish(board_topic(column.board_id), Patches(request).refresh(zone="board"))


def _has_module_assets(collector: object) -> bool:
    scripts = collector.assets_in_slot("scripts")  # type: ignore[attr-defined]
    return any(asset.kind == "jsx" for asset in scripts)


def _dev_origin() -> str:
    """Return the origin the static backend resolves jsx assets against."""
    for backend in next_framework_settings.STATIC_BACKENDS:
        origin = (backend.get("OPTIONS") or {}).get("DEV_ORIGIN", "")
        if origin:
            return str(origin)
    return ""


def inject_vite_dev_assets(sender: object, **kwargs) -> None:
    """Prepend the React Refresh preamble and the Vite HMR client before jsx modules."""
    origin = _dev_origin()
    if not origin or not _has_module_assets(sender):
        return
    preamble_code = (
        f'import RefreshRuntime from "{origin}/@react-refresh";'
        "RefreshRuntime.injectIntoGlobalHook(window);"
        "window.$RefreshReg$ = () => {};"
        "window.$RefreshSig$ = () => (type) => type;"
        "window.__vite_plugin_react_preamble_installed__ = true;"
    )
    preamble_url = (
        "data:text/javascript;base64,"
        + base64.b64encode(preamble_code.encode()).decode()
    )
    # Prepends stack, so the order is preamble, @vite/client, module scripts.
    sender.add(  # type: ignore[attr-defined]
        StaticAsset(url=preamble_url, kind="module"), prepend=True
    )
    sender.add(  # type: ignore[attr-defined]
        StaticAsset(url=f"{origin}/@vite/client", kind="module"), prepend=True
    )


if settings.DEBUG:
    collector_finalized.connect(inject_vite_dev_assets)
