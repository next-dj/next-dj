from django.http import HttpRequest
from django.utils import timezone


def render(_request: HttpRequest) -> str:
    """Return a small UTC clock badge (DI still injects ``HttpRequest`` by type)."""
    now = timezone.now()
    return (
        f'<span class="badge text-bg-light border ms-2" '
        f'title="Rendered by component.render()">{now:%H:%M:%S} UTC</span>'
    )
