from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from django.dispatch import receiver

from next.pages.signals import template_loaded


if TYPE_CHECKING:
    from pathlib import Path


_loader_hits: dict[str, str] = {}
_lock = threading.Lock()


def _detect_source(file_path: Path) -> str:
    """Return the sibling source file that actually backs this page."""
    if (file_path.parent / "template.md").exists():
        return "template.md (MarkdownTemplateLoader)"
    if (file_path.parent / "template.djx").exists():
        return "template.djx (DjxTemplateLoader)"
    return "page.py (inline render/template)"


@receiver(template_loaded)
def _on_template_loaded(file_path: Path, **kwargs) -> None:
    """Record which source type won the loader race for `file_path`."""
    with _lock:
        _loader_hits[str(file_path)] = _detect_source(file_path)


def loader_hits() -> dict[str, str]:
    """Return `{page_path: source_description}` for every resolved page."""
    with _lock:
        return dict(_loader_hits)
