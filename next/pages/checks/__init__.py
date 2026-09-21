"""System checks for the pages subsystem.

Importing the package registers every check, and each submodule names the ids it owns.
"""

from __future__ import annotations

from .contexts import (
    check_context_functions,
    check_context_registration_files,
    check_single_keyless_context,
)
from .layouts import check_layout_templates
from .loaders import check_template_loaders
from .modules import check_page_functions, check_page_module_imports
from .processors import (
    REQUEST_CONTEXT_PROCESSOR,
    check_context_processor_signature,
    check_request_in_context,
)
from .structure import check_pages_structure, check_unrouted_working_directory_pages
from .zones import check_context_reads_foreign_zone


__all__ = [
    "check_context_functions",
    "check_context_processor_signature",
    "check_context_reads_foreign_zone",
    "check_context_registration_files",
    "check_layout_templates",
    "check_page_functions",
    "check_page_module_imports",
    "check_pages_structure",
    "check_request_in_context",
    "check_single_keyless_context",
    "check_template_loaders",
    "check_unrouted_working_directory_pages",
]
