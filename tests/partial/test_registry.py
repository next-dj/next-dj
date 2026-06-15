from django.template import Context
from django.template.base import Template
from django.test import RequestFactory

from next.partial import ZoneInfo, register_patch_op, zone_requested, zones_of
from next.partial.registry import BUILTIN_OPS, PatchOpRegistry, patch_op_registry
from next.partial.signals import patch_op_registered, zone_registered


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


def _zoned_template() -> Template:
    source = (
        'x {% zone "first" %}<p>{{ a }}</p>{% endzone %} '
        '{% zone "second" tag="tbody" lazy="load" %}'
        "body{% placeholder %}ph{% endzone %}"
    )
    return Template(source)


class TestZonesOf:
    """`zones_of` derives the named zones of a compiled template."""

    def test_indexes_zones_by_name(self) -> None:
        zones = zones_of(_zoned_template())
        assert set(zones) == {"first", "second"}
        assert isinstance(zones["first"], ZoneInfo)

    def test_carries_tag_and_lazy(self) -> None:
        zones = zones_of(_zoned_template())
        assert zones["first"].tag == "div"
        assert zones["first"].lazy is None
        assert zones["second"].tag == "tbody"
        assert zones["second"].lazy == "load"

    def test_empty_template_has_no_zones(self) -> None:
        assert zones_of(Template("plain {{ x }}")) == {}

    def test_memoised_per_object(self) -> None:
        template = _zoned_template()
        assert zones_of(template) is zones_of(template)

    def test_distinct_objects_get_distinct_entries(self) -> None:
        first = zones_of(_zoned_template())
        second = zones_of(_zoned_template())
        assert first is not second
        assert set(first) == set(second)

    def test_partial_renders_standalone(self) -> None:
        zones = zones_of(_zoned_template())
        out = zones["first"].partial.render(Context({"a": "deep"}))
        assert out == "<p>deep</p>"


class TestZoneRegisteredSignal:
    """`zone_registered` fires once per source on the first read."""

    def test_fires_once_per_object(self) -> None:
        seen: list[dict[str, object]] = []

        def receiver(sender: object, **kwargs: object) -> None:
            seen.append({"sender": sender, **kwargs})

        zone_registered.connect(receiver)
        try:
            template = _zoned_template()
            zones_of(template)
            zones_of(template)
        finally:
            zone_registered.disconnect(receiver)

        names = sorted(str(entry["zone_name"]) for entry in seen)
        assert names == ["first", "second"]

    def test_quiet_without_receivers(self) -> None:
        assert zones_of(_zoned_template())


class TestZoneRequested:
    """`zone_requested` reads the partial intent of the request."""

    def test_named_zone_is_requested(self) -> None:
        request = RequestFactory().get(
            "/",
            HTTP_X_NEXT_REQUEST="1",
            HTTP_X_NEXT_ZONE="first, second",
        )
        assert zone_requested(request, "first") is True
        assert zone_requested(request, "missing") is False

    def test_non_partial_request_names_nothing(self) -> None:
        request = RequestFactory().get("/")
        assert zone_requested(request, "first") is False
