from __future__ import annotations

from typing import TYPE_CHECKING

from blog.markdown_template import render_markdown
from next.pages.loaders import TemplateLoader


if TYPE_CHECKING:
    from pathlib import Path


class MarkdownTemplateLoader(TemplateLoader):
    """Render a sibling `template.md` file as Markdown and serve it as the page body."""

    source_name = "template.md"

    def can_load(self, file_path: Path) -> bool:
        """Return True when a sibling `template.md` is present."""
        return (file_path.parent / "template.md").exists()

    def load_template(self, file_path: Path) -> str | None:
        """Return the Markdown rendered to HTML. Return `None` on read error."""
        md_file = file_path.parent / "template.md"
        try:
            return render_markdown(md_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            return None

    def source_path(self, file_path: Path) -> Path | None:
        """Return the sibling `template.md` path for stale-cache detection."""
        md_file = file_path.parent / "template.md"
        return md_file if md_file.exists() else None
