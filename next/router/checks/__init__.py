"""System checks for the `ROUTER` scope, its seams, layouts and page templates.

Importing the package registers every check.
"""

from .layouts import check_layout_page_reads, check_layout_slots
from .pages import check_no_store_pages, check_router_declarations
from .paths import check_root_layout_paths
from .prefixes import check_exclude_prefixes
from .scope import check_router_scope, check_stranded_pages
from .scripts import check_inline_scripts
from .settings import check_router_seams, check_router_settings, check_speculation


__all__ = [
    "check_exclude_prefixes",
    "check_inline_scripts",
    "check_layout_page_reads",
    "check_layout_slots",
    "check_no_store_pages",
    "check_root_layout_paths",
    "check_router_declarations",
    "check_router_scope",
    "check_router_seams",
    "check_router_settings",
    "check_speculation",
    "check_stranded_pages",
]
