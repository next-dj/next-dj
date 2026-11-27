"""Whether next.dj compresses a page, as its `page.py` declares by `page.compress`."""

from next.conf.shapes import flag

from .declarations import PageDeclaration


def _enabled(*, enabled: bool) -> bool | None:
    """Return the choice `enabled` makes, raising `ValueError` for a non-bool."""
    if not flag(enabled):
        msg = f"enabled must be True or False, got {enabled!r}"
        raise ValueError(msg)
    return enabled


compress_declaration = PageDeclaration("compress", _enabled, None)
"""The `page.compress` call, which lets next.dj compress its page or keeps it plain."""

page_compress = compress_declaration.of
"""Return the compression choice of the page at a `page.py` path, `None` for none."""


__all__ = ["compress_declaration", "page_compress"]
