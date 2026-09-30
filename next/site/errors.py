"""The public exceptions of the site area."""


class SiteOriginError(ValueError):
    """An absolute URL had neither a request nor a site URL to take its origin from."""

    def __init__(self, url: str | None = None) -> None:
        """Compose the message, naming the URL that stayed relative when one did."""
        subject = "an absolute URL" if url is None else f"making {url!r} absolute"
        super().__init__(
            f"{subject} needs a request or NEXT_FRAMEWORK['SITE']['URL'] for its origin"
        )
        self.url = url


__all__ = ["SiteOriginError"]
