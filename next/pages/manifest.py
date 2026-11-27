"""The startup wiring of pages, their caches, scan port and declarations."""

from next.manifest import Bind, Manifest
from next.ports import page_scan_slot

from . import checks, signals
from .compression import compress_declaration
from .defer import defer_declaration
from .ports import PageScanImpl
from .transactions import transaction_declaration


manifest = Manifest(
    "pages",
    ports=(Bind(page_scan_slot, PageScanImpl()),),
    declarations=(transaction_declaration, compress_declaration, defer_declaration),
    checks=checks,
    signals=signals,
)


__all__ = ["manifest"]
