from django.template import Context, Template
from flags.models import Flag
from flags.providers import DFlag


_BANNER = Template(
    '<article data-feature-guard="{{ flag.name }}"'
    ' class="rounded-xl border border-emerald-300 bg-emerald-50 p-4 shadow-sm">'
    '<div class="flex items-center justify-between">'
    '<h3 class="font-semibold text-emerald-900">✨ {{ label }}</h3>'
    '<span class="rounded-full bg-emerald-200 px-2 py-0.5 text-xs font-medium text-emerald-900">live</span>'
    "</div>"
    '<p class="mt-1 text-sm text-emerald-800">{{ description }}</p>'
    "</article>",
)


def render(flag: DFlag[Flag]) -> str:
    """Return the gated banner when the flag is enabled, otherwise empty."""
    if not flag.enabled:
        return ""
    return _BANNER.render(
        Context(
            {
                "flag": flag,
                "label": flag.label or flag.name,
                "description": flag.description or "No description provided.",
            },
        ),
    )
