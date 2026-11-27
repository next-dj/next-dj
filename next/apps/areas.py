"""The manifest of every area, in the order `NextFrameworkConfig.ready` applies them.

The order is explicit and significant: a signal reaches the receivers of an earlier
area first, and lifecycle participants open their blocks in this order.
"""

from types import ModuleType
from typing import Final

from next.boundaries.manifest import manifest as boundaries
from next.bridge.manifest import manifest as bridge
from next.components.manifest import manifest as components
from next.compression.manifest import manifest as compression
from next.conf.manifest import manifest as conf
from next.consent.manifest import manifest as consent
from next.defer.manifest import manifest as defer
from next.deps.manifest import manifest as deps
from next.forms.manifest import manifest as forms
from next.manifest import Manifest
from next.pages.manifest import manifest as pages
from next.partial.manifest import manifest as partial
from next.permissions.manifest import manifest as permissions
from next.realtime.manifest import manifest as realtime
from next.router.manifest import manifest as router
from next.scripts.manifest import manifest as scripts
from next.seo.manifest import manifest as seo
from next.server.manifest import manifest as server
from next.site.manifest import manifest as site
from next.static.manifest import manifest as static
from next.transactions.manifest import manifest as transactions
from next.urls.manifest import manifest as urls
from next.views.manifest import manifest as views

from . import staticfiles, templates


ROOT: Final = Manifest("next")
"""The flat modules at the root of the package, whose memos attach to their sources."""

APPS: Final = Manifest(
    "apps",
    setting_changed=(staticfiles.reinstall_finders, templates.reinstall_builtins),
)
"""The hooks of the app itself, reinstalled last once a setting they read changes."""

MANIFESTS: Final[tuple[Manifest, ...]] = (
    conf,
    ROOT,
    site,
    bridge,
    compression,
    permissions,
    transactions,
    deps,
    boundaries,
    components,
    pages,
    static,
    forms,
    urls,
    partial,
    consent,
    realtime,
    scripts,
    seo,
    defer,
    router,
    server,
    views,
    APPS,
)

CHECK_MODULES: Final[tuple[ModuleType, ...]] = tuple(
    manifest.checks for manifest in MANIFESTS if manifest.checks is not None
)
"""Every area's checks module, imported so its checks register.

`next.apps.checks` is not among them, as `next.apps` imports it itself.
"""


__all__ = ["APPS", "CHECK_MODULES", "MANIFESTS", "ROOT"]
