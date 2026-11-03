from pathlib import Path

from django.http import HttpRequest

from next.components import get_component, render_component


def render_fragment(
    name: str, template_path: Path, request: HttpRequest | None = None, **props
) -> str:
    """Render one component by name for a patch payload, resolved from the page path."""
    info = get_component(name, template_path)
    if info is None:
        msg = f"No component named {name!r} is visible from {template_path}."
        raise LookupError(msg)
    return render_component(
        info, {**props, "current_template_path": template_path}, request
    )
