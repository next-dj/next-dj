"""The public exceptions of the seo area."""

from pathlib import Path


class SitemapOriginError(ValueError):
    """A request-free sitemap build has no origin to make its URLs absolute."""

    def __init__(self, root: Path) -> None:
        """Name the page tree whose sitemap could not be built."""
        super().__init__(
            f"the sitemap of {root} needs a request or "
            "NEXT_FRAMEWORK['METADATA']['DEFAULTS']['base'] to build absolute URLs"
        )
        self.root = root


class SitemapTrailError(ValueError):
    """`@sitemap.items` names a trail the tree of its `sitemap.py` does not route."""

    def __init__(self, file: Path, trail: str) -> None:
        """Name the `sitemap.py` and the trail no page beside it answers to."""
        super().__init__(
            f"@sitemap.items({trail!r}) in {file} names a trail "
            f"no page under {file.parent} routes"
        )
        self.file = file
        self.trail = trail


__all__ = ["SitemapOriginError", "SitemapTrailError"]
