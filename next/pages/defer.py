"""The boundary a `page.py` declares for its own page body through `page.defer`."""

from next.boundaries.options import BoundaryOptions, DeferMode

from .declarations import PageDeclaration


def _options(
    *, timeout: float | str | None = None, parallel: bool = True
) -> BoundaryOptions | None:
    """Return the options of a streamed page body, a misfit raising `ValueError`."""
    return BoundaryOptions.parse(DeferMode.STREAM, timeout=timeout, parallel=parallel)


defer_declaration = PageDeclaration("defer", _options, None)
"""The `page.defer` call, bounding the streamed body of its page and placing its job."""

page_defer = defer_declaration.of
"""Return the boundary options of the page at a `page.py` path, `None` for none."""


__all__ = ["defer_declaration", "page_defer"]
