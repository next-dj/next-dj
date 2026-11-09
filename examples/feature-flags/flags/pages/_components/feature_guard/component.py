from django.utils.html import format_html
from flags.deps import DFlag
from flags.models import Flag


_BANNER = (
    '<article data-feature-guard="{name}"'
    ' class="rounded-xl border border-emerald-300 bg-emerald-50 p-4 shadow-sm">'
    '<div class="flex items-center justify-between">'
    '<h3 class="font-semibold text-emerald-900">{label}</h3>'
    '<span class="rounded-full bg-emerald-200 px-2 py-0.5 text-xs'
    ' font-medium text-emerald-900">live</span>'
    "</div>"
    '<p class="mt-1 text-sm text-emerald-800">{description}</p>'
    "</article>"
)


def render(flag: DFlag[Flag]) -> str:
    """Return the gated banner when the flag is enabled, otherwise empty."""
    if not flag.enabled:
        return ""
    return format_html(
        _BANNER,
        name=flag.name,
        label=flag.label or flag.name,
        description=flag.description or "No description provided.",
    )
