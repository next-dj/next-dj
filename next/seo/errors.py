"""The public exceptions of the seo area."""

from pathlib import Path


class SitemapTrailError(ValueError):
    """`@sitemap.items` names a trail the tree of its `sitemap.py` does not route."""

    def __init__(self, file: Path, trail: str) -> None:
        """Build the message from the `sitemap.py` path and the unrouted trail."""
        super().__init__(
            f"@sitemap.items({trail!r}) in {file} names a trail "
            f"no page under {file.parent} routes"
        )
        self.file = file
        self.trail = trail


class SitemapEntryError(ValueError):
    """A `SitemapEntry` field holds a value the sitemap protocol does not allow."""

    def __init__(self, value: object, *, field: str, expected: str) -> None:
        """Build the message from the field name, its value and the expected shape."""
        super().__init__(f"SitemapEntry.{field} is {value!r}, expected {expected}")
        self.field = field
        self.value = value


class RobotsRuleError(ValueError):
    """A `RobotsRule` field holds a value that would break the robots.txt grammar."""

    def __init__(self, value: object, *, field: str, expected: str) -> None:
        """Build the message from the field name, its value and the expected shape."""
        super().__init__(f"RobotsRule.{field} is {value!r}, expected {expected}")
        self.field = field
        self.value = value


class SeoSourceImportError(Exception):
    """A `sitemap.py` or a `robots.py` raised an exception during import.

    The original exception is `__cause__`, and `path` is the failing file.
    """

    def __init__(self, path: Path) -> None:
        """Build the message from the failing file path."""
        super().__init__(f"{path} failed to import")
        self.path = path


__all__ = [
    "RobotsRuleError",
    "SeoSourceImportError",
    "SitemapEntryError",
    "SitemapTrailError",
]
