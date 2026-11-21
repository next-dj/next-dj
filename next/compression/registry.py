"""The codec backends of the process in settings order, indexed by coding."""

from collections.abc import Iterator

from .backends import CompressionBackend


class CompressionRegistry:
    """Hold the loaded backends in order, the first one of a coding answering it."""

    __slots__ = ("_by_coding", "_ordered", "_version")

    def __init__(self) -> None:
        """Start empty."""
        self._ordered: list[CompressionBackend] = []
        self._by_coding: dict[str, CompressionBackend] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every registration."""
        return self._version

    def register(self, backend: CompressionBackend) -> bool:
        """Add `backend`, answering whether it took its coding, unclaimed until now."""
        if backend.coding in self._by_coding:
            return False
        self._ordered.append(backend)
        self._by_coding[backend.coding] = backend
        self._version += 1
        return True

    def get(self, coding: str) -> CompressionBackend | None:
        """Return the backend of `coding`, `None` when none was loaded."""
        return self._by_coding.get(coding)

    def codings(self) -> tuple[str, ...]:
        """Return the codings of the loaded backends in settings order."""
        return tuple(backend.coding for backend in self._ordered)

    def __iter__(self) -> Iterator[CompressionBackend]:
        """Iterate the backends in settings order."""
        return iter(tuple(self._ordered))

    def __len__(self) -> int:
        """Return how many backends are loaded."""
        return len(self._ordered)


__all__ = ["CompressionRegistry"]
