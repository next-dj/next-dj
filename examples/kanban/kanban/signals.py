import os

from django.conf import settings

from next.static.assets import StaticAsset
from next.static.signals import collector_finalized


def _has_module_assets(collector: object) -> bool:
    scripts = collector.assets_in_slot("scripts")  # type: ignore[attr-defined]
    return any(asset.kind == "jsx" for asset in scripts)


def inject_vite_dev_assets(sender: object, **_kwargs: object) -> None:
    """Prepend the Vite HMR client and React Refresh preamble.

    Only fires when the page actually carries jsx scripts so the index
    page stays free of Vite dev plumbing.
    """
    if not _has_module_assets(sender):
        return
    origin = os.environ.get("VITE_ORIGIN", "http://localhost:5173")
    preamble = (
        '<script type="module">'
        f'import RefreshRuntime from "{origin}/@react-refresh";'
        "RefreshRuntime.injectIntoGlobalHook(window);"
        "window.$RefreshReg$ = () => {};"
        "window.$RefreshSig$ = () => (type) => type;"
        "window.__vite_plugin_react_preamble_installed__ = true;"
        "</script>"
    )
    sender.add(  # type: ignore[attr-defined]
        StaticAsset(
            url="",
            kind="js",
            inline=f'<script type="module" src="{origin}/@vite/client"></script>',
        ),
        prepend=True,
    )
    sender.add(  # type: ignore[attr-defined]
        StaticAsset(url="", kind="js", inline=preamble),
        prepend=True,
    )


if settings.DEBUG:
    collector_finalized.connect(inject_vite_dev_assets)
