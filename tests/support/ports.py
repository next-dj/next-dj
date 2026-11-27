import inspect
from pathlib import Path

from django.http import HttpRequest

from next.static import StaticCollector


def port_methods(port: type) -> list[str]:
    """Return the public method names `port` declares itself, sorted."""
    return sorted(
        name
        for name, member in vars(port).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )


def call_shape(owner: type, name: str) -> list[tuple[str, object]]:
    """Return the parameter names and kinds, leaving out defaults an override adds."""
    signature = inspect.signature(getattr(owner, name))
    return [(param.name, param.kind) for param in signature.parameters.values()]


def parameter_shape(owner: type, name: str) -> list[tuple[str, object, object]]:
    """Return the parameter names, kinds and defaults of `owner.name`."""
    signature = inspect.signature(getattr(owner, name))
    return [
        (param.name, param.kind, param.default)
        for param in signature.parameters.values()
    ]


class StubRender:
    """A `ScriptRender` a provider test hands its share, its bundles at `/b/<name>`."""

    def __init__(
        self,
        *,
        collector: StaticCollector | None = None,
        request: HttpRequest | None = None,
        page_path: Path | None = None,
        runtime: bool = True,
        streams: bool = False,
        nonce: str | None = None,
        bundles: bool = True,
    ) -> None:
        """Hold the render facts, a bare collector unless one is given."""
        self.collector = collector or StaticCollector()
        self.request = request
        self.page_path = page_path
        self.runtime = runtime
        self.streams = streams
        self.entries: dict[str, object] = {}
        self._nonce = nonce
        self._bundles = bundles
        self.minted = 0

    def nonce(self) -> str | None:
        self.minted += 1
        return self._nonce

    def bundle_url(self, name: str) -> str | None:
        return f"/b/{name}" if self._bundles else None
