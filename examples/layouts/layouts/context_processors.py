"""Context processors for the layouts example.

This module provides context processors that add variables to all page templates
when using NEXT_PAGES with context_processors support.
"""

from django.conf import settings
from django.http import HttpRequest


def site_info(_request: HttpRequest) -> dict[str, str | bool | int]:
    """Add site information to the template context.

    This context processor adds site-wide information that can be used
    in all page templates, such as site name, version, and other
    configuration values.
    """
    return {
        "site_name": "next-dj layouts example",
        "site_version": "0.1.0",
        "debug_mode": settings.DEBUG,
        "current_year": 2025,
    }
