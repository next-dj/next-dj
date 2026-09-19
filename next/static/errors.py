"""Exceptions the static area raises for an asset reference it refuses to serve."""

from __future__ import annotations

from django.core.exceptions import SuspiciousFileOperation


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


class StaticAssetTraversalError(SuspiciousFileOperation):
    """Raised when an asset reference walks above the staticfiles root.

    Django reads the base class as a bad request, which is what a stored or
    user-supplied reference leaving the static tree deserves.
    """

    def __init__(self, reference: str) -> None:
        """Store the offending reference and build the message naming the rule."""
        self.reference = reference
        super().__init__(
            f"Static asset reference {reference!r} walks above the staticfiles "
            "root. A name is relative to that root, so resolve the reference "
            "to a name inside it or pass a ready URL."
        )


__all__ = ["StaticAssetNotFoundError", "StaticAssetTraversalError"]
