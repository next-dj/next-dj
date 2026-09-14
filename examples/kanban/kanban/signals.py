import base64

from django.conf import settings

from next.conf import next_framework_settings
from next.static import StaticAsset
from next.static.signals import collector_finalized


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
    """Prepend the React Refresh preamble and the Vite HMR client.

    Both assets are URL-form module scripts because the collector force-appends inline
    ones, and `@vitejs/plugin-react` needs the preamble to run before any jsx module.
    Base64 in a `data:` URL keeps the preamble a single self-contained script tag.
    """
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
