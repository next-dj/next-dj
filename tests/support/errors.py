class BackendDownError(Exception):
    """A failure a dependency or a batch function raises, distinct from the library's."""


class HaltError(BaseException):
    """An interrupt-like failure that is not an `Exception`."""
