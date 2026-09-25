import pytest
from django.template import Context
from django.template.base import Template
from django.test import RequestFactory

from next.partial import register_patch_op, zone_requested
from next.partial.registry import (
    BUILTIN_OPS,
    PatchOpRegistry,
    ZoneInfo,
    patch_op_registry,
    zones_of,
)
from next.partial.signals import patch_op_registered, zone_registered
from next.partial.zone import ZoneOptions
from next.testing import capture_signals


class TestBuiltinOps:
    """The registry seeds itself with the built-in protocol verbs."""

    def test_morph_is_builtin(self) -> None:
        assert "morph" in BUILTIN_OPS

    def test_core_verbs_present(self) -> None:
        for verb in ("replace", "inner", "remove", "event", "toast", "meta"):
            assert verb in BUILTIN_OPS

    def test_layer_verbs_present(self) -> None:
        assert "layer.open" in BUILTIN_OPS
        assert "layer.close" in BUILTIN_OPS

    def test_fresh_registry_knows_builtins(self) -> None:
        registry = PatchOpRegistry()
        assert all(verb in registry for verb in BUILTIN_OPS)
        assert registry.custom_names() == frozenset()


class TestRegisterPatchOp:
    """Registering a custom verb makes it known and announces it."""

    def test_register_makes_verb_known(self) -> None:
        registry = PatchOpRegistry()
        assert "confetti" not in registry
        registry.register("confetti")
        assert "confetti" in registry

    def test_register_emits_signal(self) -> None:
        with capture_signals(patch_op_registered) as recorded:
            PatchOpRegistry().register("confetti")
        assert len(recorded) == 1
        event = recorded.first_for(patch_op_registered)
        assert event.sender is PatchOpRegistry
        assert event.kwargs["name"] == "confetti"

    def test_register_records_the_name_with_no_receiver_connected(self) -> None:
        registry = PatchOpRegistry()
        registry.register("quiet")
        assert "quiet" in registry

    @pytest.mark.usefixtures("restored_op_registry")
    def test_facade_register_uses_global_registry(self) -> None:
        register_patch_op("spark")
        assert "spark" in patch_op_registry

    def test_custom_names_reports_what_the_project_registered(self) -> None:
        registry = PatchOpRegistry()
        registry.register("confetti")
        assert registry.custom_names() == frozenset({"confetti"})

    def test_a_name_shadowing_a_builtin_stays_visible(self) -> None:
        # the check that reports the shadowing reads custom_names, so a
        # registration dropped here would leave it with nothing to report
        registry = PatchOpRegistry()
        registry.register("morph")
        assert registry.custom_names() == frozenset({"morph"})


class TestRegistryRecords:
    """The registry keeps a name index and a version behind the names it reports."""

    def test_a_fresh_registry_holds_nothing_at_version_zero(self) -> None:
        registry = PatchOpRegistry()
        assert registry.custom_names() == frozenset()
        assert registry.version == 0

    def test_custom_names_stays_an_unordered_frozenset(self) -> None:
        registry = PatchOpRegistry()
        registry.register("zeta")
        registry.register("alpha")
        assert registry.custom_names() == frozenset({"zeta", "alpha"})

    def test_a_new_verb_bumps_the_version(self) -> None:
        registry = PatchOpRegistry()
        registry.register("confetti")
        assert registry.version == 1
        registry.register("sparkle")
        assert registry.version == 2

    def test_re_registering_a_held_verb_leaves_the_version_alone(self) -> None:
        # a reload re-runs every register() call, and a bumped version there would
        # invalidate every consumer cache keyed on it for no change at all
        registry = PatchOpRegistry()
        registry.register("confetti")
        registry.register("confetti")
        assert registry.version == 1

    def test_re_registering_a_held_verb_does_not_duplicate_it(self) -> None:
        registry = PatchOpRegistry()
        registry.register("confetti")
        registry.register("confetti")
        assert registry.custom_names() == frozenset({"confetti"})

    def test_re_registering_a_held_verb_still_announces_it(self) -> None:
        # the signal is how a late subscriber learns the verb exists, so it fires
        # on the repeat call even though the records do not change
        registry = PatchOpRegistry()
        registry.register("confetti")
        with capture_signals(patch_op_registered) as recorded:
            registry.register("confetti")
        assert [event.kwargs["name"] for event in recorded] == ["confetti"]


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

    def test_carries_no_poll_without_the_kwarg(self) -> None:
        zones = zones_of(_zoned_template())
        assert zones["first"].poll is None
        assert zones["second"].poll is None

    def test_zone_info_carries_poll(self) -> None:
        template = Template('{% zone "z" poll="5s" %}b{% endzone %}')
        info = zones_of(template)["z"]
        assert info.poll == 5000

    def test_zone_info_carries_structured_options(self) -> None:
        zones = zones_of(_zoned_template())
        assert zones["first"].options == ZoneOptions()
        assert zones["second"].options == ZoneOptions(tag="tbody", lazy="load")

    def test_zone_info_scalars_delegate_to_options(self) -> None:
        zones = zones_of(_zoned_template())
        info = zones["second"]
        assert info.lazy == info.options.lazy
        assert info.poll == info.options.poll
        assert info.tag == info.options.tag

    def test_zone_info_options_carry_the_delivery_attrs(self) -> None:
        template = Template('{% zone "z" poll="5s" %}b{% endzone %}')
        info = zones_of(template)["z"]
        assert info.options == ZoneOptions(poll=5000)
        assert info.options.delivery_attrs == ' data-next-poll="5000"'

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


def _nested_template() -> Template:
    source = (
        '{% zone "outer" %}<p>{{ a }}</p>'
        '{% zone "inner" %}<b>{{ b }}</b>'
        '{% zone "deep" %}<i>{{ c }}</i>{% endzone %}'
        "{% endzone %}"
        "{% endzone %}"
    )
    return Template(source)


class TestZoneInfoNested:
    """`ZoneInfo.nested` names the zones declared inside the zone body."""

    def test_leaf_zone_has_no_nested_names(self) -> None:
        zones = zones_of(_nested_template())
        assert zones["deep"].nested == frozenset()

    def test_zone_names_its_direct_child(self) -> None:
        zones = zones_of(_nested_template())
        assert zones["inner"].nested == frozenset({"deep"})

    def test_nesting_is_transitive(self) -> None:
        zones = zones_of(_nested_template())
        assert zones["outer"].nested == frozenset({"inner", "deep"})

    def test_placeholder_branch_stays_out(self) -> None:
        template = Template(
            '{% zone "host" lazy="load" %}body{% placeholder %}'
            '{% zone "ph" %}x{% endzone %}{% endzone %}'
        )
        zones = zones_of(template)
        assert set(zones) == {"host", "ph"}
        assert zones["host"].nested == frozenset()

    def test_plain_zone_map_carries_empty_nested(self) -> None:
        zones = zones_of(_zoned_template())
        assert zones["first"].nested == frozenset()
        assert zones["second"].nested == frozenset()


class TestZoneRegisteredSignal:
    """`zone_registered` fires once per source on the first read."""

    def test_fires_once_per_object(self) -> None:
        template = _zoned_template()
        with capture_signals(zone_registered) as recorded:
            zones_of(template)
            zones_of(template)
        names = sorted(str(event.kwargs["zone_name"]) for event in recorded)
        assert names == ["first", "second"]

    def test_sends_lazy_and_poll_kwargs(self) -> None:
        with capture_signals(zone_registered) as recorded:
            zones_of(Template('{% zone "z" poll="5s" %}b{% endzone %}'))
        assert len(recorded) == 1
        event = recorded.first_for(zone_registered)
        assert event.kwargs["zone_name"] == "z"
        assert event.kwargs["lazy"] is None
        assert event.kwargs["poll"] == 5000

    def test_quiet_without_receivers(self) -> None:
        assert zones_of(_zoned_template())


class TestZoneRequested:
    """`zone_requested` reads the partial intent of the request."""

    def test_named_zone_is_requested(self) -> None:
        request = RequestFactory().get(
            "/", HTTP_X_NEXT_REQUEST="1", HTTP_X_NEXT_ZONE="first, second"
        )
        assert zone_requested(request, "first") is True
        assert zone_requested(request, "missing") is False

    def test_non_partial_request_names_nothing(self) -> None:
        request = RequestFactory().get("/")
        assert zone_requested(request, "first") is False
