"""Catalogue of every area's checks module, imported so its checks register."""

from types import ModuleType

from next.components import checks as components_checks
from next.compression import checks as compression_checks
from next.conf import checks as conf_checks
from next.consent import checks as consent_checks
from next.deps import checks as deps_checks
from next.forms import checks as forms_checks
from next.pages import checks as pages_checks
from next.partial import checks as partial_checks
from next.permissions import checks as permissions_checks
from next.realtime import checks as realtime_checks
from next.router import checks as router_checks
from next.scripts import checks as scripts_checks
from next.seo import checks as seo_checks
from next.server import checks as server_checks
from next.site import checks as site_checks
from next.static import checks as static_checks
from next.suspense import checks as suspense_checks
from next.transactions import checks as transactions_checks
from next.urls import checks as urls_checks


CHECK_MODULES: tuple[ModuleType, ...] = (
    components_checks,
    compression_checks,
    conf_checks,
    consent_checks,
    deps_checks,
    forms_checks,
    pages_checks,
    partial_checks,
    permissions_checks,
    realtime_checks,
    router_checks,
    scripts_checks,
    seo_checks,
    server_checks,
    site_checks,
    static_checks,
    suspense_checks,
    transactions_checks,
    urls_checks,
)
"""Every area's checks module except `next.apps.checks`, which `next.apps` imports."""


__all__ = ["CHECK_MODULES"]
