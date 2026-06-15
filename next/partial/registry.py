"""Registry of patch verbs known to the builder side of the protocol."""

from .signals import patch_op_registered


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
    }
)


class PatchOpRegistry:
    """Registry of patch verbs known to the builder.

    The built-in verbs seed the registry so the core eats its own dog
    food. A project registers a custom verb to clear the `next.E066`
    check and earn the generic `op()` channel on the builder.
    """

    def __init__(self) -> None:
        """Seed the registry with the built-in verbs."""
        self._ops: set[str] = set(BUILTIN_OPS)

    def register(self, name: str) -> None:
        """Register a custom verb and announce it to subscribers."""
        self._ops.add(name)
        if patch_op_registered.receivers:
            patch_op_registered.send(sender=type(self), name=name)

    def is_registered(self, name: str) -> bool:
        """Return True when the verb is known to the registry."""
        return name in self._ops

    def names(self) -> frozenset[str]:
        """Return the set of registered verb names."""
        return frozenset(self._ops)


patch_op_registry = PatchOpRegistry()


def register_patch_op(name: str) -> None:
    """Register a custom patch verb with the builder side of the protocol."""
    patch_op_registry.register(name)


__all__ = [
    "BUILTIN_OPS",
    "PatchOpRegistry",
    "patch_op_registry",
    "register_patch_op",
]
