from next.partial import register_patch_op
from next.partial.registry import BUILTIN_OPS, PatchOpRegistry, patch_op_registry
from next.partial.signals import patch_op_registered


class TestBuiltinOps:
    """The registry seeds itself with the built-in protocol verbs."""

    def test_morph_is_builtin(self) -> None:
        assert "morph" in BUILTIN_OPS

    def test_core_verbs_present(self) -> None:
        for verb in ("replace", "inner", "remove", "event", "toast"):
            assert verb in BUILTIN_OPS

    def test_layer_verbs_present(self) -> None:
        assert "layer.open" in BUILTIN_OPS
        assert "layer.close" in BUILTIN_OPS

    def test_fresh_registry_knows_builtins(self) -> None:
        registry = PatchOpRegistry()
        assert registry.is_registered("morph")
        assert registry.names() == BUILTIN_OPS


class TestRegisterPatchOp:
    """Registering a custom verb makes it known and announces it."""

    def test_register_makes_verb_known(self) -> None:
        registry = PatchOpRegistry()
        assert not registry.is_registered("confetti")
        registry.register("confetti")
        assert registry.is_registered("confetti")

    def test_register_emits_signal(self) -> None:
        seen: list[dict[str, object]] = []

        def receiver(sender: object, **kwargs: object) -> None:
            seen.append({"sender": sender, **kwargs})

        patch_op_registered.connect(receiver)
        try:
            registry = PatchOpRegistry()
            registry.register("confetti")
        finally:
            patch_op_registered.disconnect(receiver)

        assert len(seen) == 1
        assert seen[0]["sender"] is PatchOpRegistry
        assert seen[0]["name"] == "confetti"

    def test_register_skips_send_without_receivers(self) -> None:
        registry = PatchOpRegistry()
        registry.register("quiet")
        assert registry.is_registered("quiet")

    def test_facade_register_uses_global_registry(self) -> None:
        register_patch_op("spark")
        assert patch_op_registry.is_registered("spark")
