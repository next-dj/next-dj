"""Registry of the patch verbs the builder side of the protocol accepts."""

from next.partial.signals import patch_op_registered


BUILTIN_OPS: frozenset[str] = frozenset(
    {
        "morph",
        "replace",
        "inner",
        "append",
        "prepend",
        "remove",
        "refresh",
        "context",
        "event",
        "toast",
        "layer.open",
        "layer.close",
        "url",
        "visit",
        "meta",
    }
)


class PatchOpRegistry:
    """Ordered record of the custom patch verbs a project adds to the built-in ones.

    Membership is what `op()` consults and the names are what the checks read.
    """

    def __init__(self) -> None:
        """Start with no custom verb on record, the built-ins seed the reads."""
        self._ordered: list[str] = []
        self._by_name: dict[str, int] = {}
        self._version = 0

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every verb this registry did not hold."""
        return self._version

    def _bump(self) -> None:
        self._version += 1

    def register(self, name: str) -> None:
        """Register a custom verb and announce it to subscribers.

        The name is recorded whatever it is, so a registration shadowing a
        built-in verb stays visible to the check that reports it.
        """
        if name not in self._by_name:
            self._by_name[name] = len(self._ordered)
            self._ordered.append(name)
            self._bump()
        patch_op_registered.send(sender=type(self), name=name)

    def __contains__(self, name: object) -> bool:
        """Return True when the verb is built in or registered by a project."""
        return name in BUILTIN_OPS or name in self._by_name

    def custom_names(self) -> frozenset[str]:
        """Return every verb name a project registered itself."""
        return frozenset(self._ordered)


patch_op_registry = PatchOpRegistry()


def register_patch_op(name: str) -> None:
    """Register a custom patch verb with the builder side of the protocol."""
    patch_op_registry.register(name)


__all__ = ["BUILTIN_OPS", "PatchOpRegistry", "patch_op_registry", "register_patch_op"]
