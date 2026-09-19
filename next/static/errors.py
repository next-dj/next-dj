"""Exceptions the static area raises for an asset staticfiles cannot resolve."""

from __future__ import annotations


class StaticAssetNotFoundError(RuntimeError):
    """Raised when a static path is absent from the Django staticfiles manifest.

    A co-located file and an authored name fail on the same terms, so both raise it.
    """

    def __init__(self, path: str) -> None:
        """Store the unresolved path and build the message naming the fix."""
        self.path = path
        super().__init__(
            f"Static asset {path!r} is missing from Django staticfiles "
            "manifest. Run collectstatic and ensure the next static "
            "finder is enabled."
        )


__all__ = ["StaticAssetNotFoundError"]
