class BackendDownError(Exception):
    """A failure a dependency or a batch function raises, distinct from the library's."""


class HaltError(BaseException):
    """An interrupt-like failure that is not an `Exception`."""


def fail_hop() -> None:
    """Raise the `ValueError` a failing hop raises."""
    msg = "hop failed"
    raise ValueError(msg)
