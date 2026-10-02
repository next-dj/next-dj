"""The public exceptions of the seo area."""

from pathlib import Path


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


class SitemapEntryError(ValueError):
    """A `SitemapEntry` carries a value the sitemap protocol has no place for."""

    def __init__(self, value: object, *, field: str, expected: str) -> None:
        """Name the field, the value it holds and the shape it takes."""
        super().__init__(f"SitemapEntry.{field} is {value!r}, expected {expected}")
        self.field = field
        self.value = value


class RobotsRuleError(ValueError):
    """A `RobotsRule` carries a value that would break the robots.txt grammar."""

    def __init__(self, value: object, *, field: str, expected: str) -> None:
        """Name the field, the value it holds and the shape it takes."""
        super().__init__(f"RobotsRule.{field} is {value!r}, expected {expected}")
        self.field = field
        self.value = value


class SeoSourceImportError(Exception):
    """A `sitemap.py` or a `robots.py` raised while importing.

    The original exception travels as `__cause__` and the offending file as `path`.
    """

    def __init__(self, path: Path) -> None:
        """Compose the message from the failing file."""
        super().__init__(f"{path} failed to import")
        self.path = path


__all__ = [
    "RobotsRuleError",
    "SeoSourceImportError",
    "SitemapEntryError",
    "SitemapTrailError",
]
