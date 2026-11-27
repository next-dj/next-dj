"""The public exceptions of the compression area."""

from django.core.exceptions import ImproperlyConfigured


class CodecUnavailableError(ImproperlyConfigured):
    """A codec was asked for a compressor while its library does not import."""

    def __init__(self, token: str) -> None:
        """Name the codec whose library is missing."""
        self.token = token
        super().__init__(
            f"The {token!r} codec has no library installed, so it cannot compress."
        )


class CompressionBackendError(ImproperlyConfigured):
    """A `COMPRESSION` path does not import or names nothing usable."""

    def __init__(self, key: str, dotted: str, problem: str) -> None:
        """Name the settings key, the path and why it is unusable."""
        self.key = key
        self.dotted = dotted
        where = f"NEXT_FRAMEWORK['COMPRESSION'][{key!r}]"
        super().__init__(f"{where} {dotted!r} {problem}.")


__all__ = ["CodecUnavailableError", "CompressionBackendError"]
