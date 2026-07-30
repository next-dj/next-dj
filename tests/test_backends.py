import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.dispatch import Signal
from django.test import override_settings

from next.backends import SingleBackendManager, load_backends, resolve_backend_class
from tests.support.backends import (
    ALPHA,
    BETA,
    CONCRETE,
    COUNTING,
    FOREIGN,
    MISSING,
    NOT_A_CLASS,
    RAISING,
    AbstractFakeBackend,
    AlphaBackend,
    BetaBackend,
    ConcreteFakeBackend,
    CountingBackend,
    FakeBackend,
)


_DICT_SETTING = "FORM_WIZARD_BACKEND"
_LIST_SETTING = "PARTIAL_BACKENDS"
_UNKNOWN_SETTING = "NOT_A_FRAMEWORK_SETTING"

_FAMILY_SHAPES = [
    pytest.param(_DICT_SETTING, lambda entry: entry, id="dict-entry"),
    pytest.param(_LIST_SETTING, lambda entry: [entry], id="list-of-entries"),
]


class _Recorder:
    """Collects what a backend-loaded signal carried to its receivers."""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object, object]] = []

    def __call__(self, sender, **kwargs) -> None:
        self.calls.append((sender, kwargs.get("config"), kwargs.get("instance")))


def _manager(
    setting: str = _DICT_SETTING, default: str | None = None
) -> SingleBackendManager[FakeBackend]:
    return SingleBackendManager(setting, base=FakeBackend, default=default)


class TestResolveBackendClass:
    """The dotted path under BACKEND resolves to a class of the family."""

    def test_valid_entry_returns_the_named_class(self) -> None:
        klass = resolve_backend_class({"BACKEND": ALPHA}, base=FakeBackend)
        assert klass is AlphaBackend

    def test_missing_backend_without_default_is_improperly_configured(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="under BACKEND"):
            resolve_backend_class({"OPTIONS": {}}, base=FakeBackend)

    def test_empty_backend_is_improperly_configured(self) -> None:
        # an empty dotted path would otherwise reach the importer and fail
        # with a message about module paths rather than about the entry
        with pytest.raises(ImproperlyConfigured, match="under BACKEND"):
            resolve_backend_class({"BACKEND": ""}, base=FakeBackend)

    def test_missing_backend_falls_back_to_the_default(self) -> None:
        klass = resolve_backend_class({}, base=FakeBackend, default=BETA)
        assert klass is BetaBackend

    def test_explicit_backend_wins_over_the_default(self) -> None:
        klass = resolve_backend_class({"BACKEND": ALPHA}, base=FakeBackend, default=BETA)
        assert klass is AlphaBackend

    def test_class_outside_the_family_is_improperly_configured(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="is not a FakeBackend subclass"):
            resolve_backend_class({"BACKEND": FOREIGN}, base=FakeBackend)

    def test_dotted_path_naming_a_function_is_improperly_configured(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="is not a FakeBackend subclass"):
            resolve_backend_class({"BACKEND": NOT_A_CLASS}, base=FakeBackend)


class TestLoadBackends:
    """A backend list is built entry by entry, bad entries costing only themselves."""

    def test_returns_instances_in_config_order(self) -> None:
        backends = load_backends(
            [{"BACKEND": BETA}, {"BACKEND": ALPHA}], base=FakeBackend
        )
        assert [type(backend) for backend in backends] == [BetaBackend, AlphaBackend]

    def test_instance_keeps_its_own_config_entry(self) -> None:
        entry = {"BACKEND": ALPHA, "OPTIONS": {"a": 1}}
        (backend,) = load_backends([entry], base=FakeBackend)
        assert backend.config == entry

    def test_entries_without_backend_use_the_default(self) -> None:
        backends = load_backends([{}], base=FakeBackend, default=ALPHA)
        assert [type(backend) for backend in backends] == [AlphaBackend]

    def test_unimportable_entry_is_logged_and_skipped(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="next.backends"):
            backends = load_backends(
                [{"BACKEND": MISSING}, {"BACKEND": ALPHA}], base=FakeBackend
            )
        assert [type(backend) for backend in backends] == [AlphaBackend]
        assert "error resolving FakeBackend from config" in caplog.text

    def test_entry_outside_the_family_is_logged_and_skipped(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="next.backends"):
            backends = load_backends(
                [{"BACKEND": FOREIGN}, {"BACKEND": ALPHA}], base=FakeBackend
            )
        assert [type(backend) for backend in backends] == [AlphaBackend]
        assert "error resolving FakeBackend from config" in caplog.text

    def test_backend_reporting_bad_config_is_logged_and_skipped(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="next.backends"):
            backends = load_backends(
                [{"BACKEND": RAISING, "ERROR": "config"}, {"BACKEND": ALPHA}],
                base=FakeBackend,
            )
        assert [type(backend) for backend in backends] == [AlphaBackend]
        assert "error creating FakeBackend from config" in caplog.text
        assert "boom" in caplog.text

    @pytest.mark.parametrize(
        ("kind", "error"),
        [("type", TypeError), ("value", ValueError), ("import", ImportError)],
    )
    def test_other_errors_from_construction_escape(self, kind, error) -> None:
        # a constructor failing for anything but its own config is a bug,
        # not an entry to skip
        with pytest.raises(error, match="boom"):
            load_backends(
                [{"BACKEND": RAISING, "ERROR": kind}, {"BACKEND": ALPHA}],
                base=FakeBackend,
            )

    def test_unexpected_error_from_construction_escapes(self) -> None:
        with pytest.raises(KeyError):
            load_backends([{"BACKEND": RAISING}], base=FakeBackend)

    def test_no_signal_sends_nothing(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        load_backends([{"BACKEND": ALPHA}], base=FakeBackend)

        assert recorder.calls == []

    def test_signal_fires_once_per_loaded_backend(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)
        entries = [{"BACKEND": ALPHA, "OPTIONS": {"a": 1}}, {"BACKEND": BETA}]

        first, second = load_backends(entries, base=FakeBackend, signal=signal)

        assert recorder.calls == [
            (AlphaBackend, entries[0], first),
            (BetaBackend, entries[1], second),
        ]

    def test_signal_carries_a_config_copy(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)
        entry = {"BACKEND": ALPHA}

        load_backends([entry], base=FakeBackend, signal=signal)

        assert recorder.calls[0][1] == entry
        assert recorder.calls[0][1] is not entry

    def test_skipped_entry_sends_no_signal(self, caplog) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        with caplog.at_level(logging.ERROR, logger="next.backends"):
            load_backends([{"BACKEND": MISSING}], base=FakeBackend, signal=signal)

        assert recorder.calls == []

    def test_empty_config_list_loads_nothing(self) -> None:
        assert load_backends([], base=FakeBackend) == []

    def test_each_entry_gets_its_own_instance(self) -> None:
        CountingBackend.instances = 0

        backends = load_backends(
            [{"BACKEND": COUNTING}, {"BACKEND": COUNTING}], base=FakeBackend
        )

        assert CountingBackend.instances == 2
        assert backends[0] is not backends[1]


class TestAbstractFamilyRoot:
    """An abstract root serves as the family base, as the real areas declare it."""

    def test_resolve_returns_the_concrete_member(self) -> None:
        klass = resolve_backend_class({"BACKEND": CONCRETE}, base=AbstractFakeBackend)
        assert klass is ConcreteFakeBackend

    def test_resolve_names_the_abstract_root_in_its_messages(self) -> None:
        with pytest.raises(
            ImproperlyConfigured, match="is not a AbstractFakeBackend subclass"
        ):
            resolve_backend_class({"BACKEND": ALPHA}, base=AbstractFakeBackend)

    def test_load_builds_an_instance_of_the_concrete_member(self) -> None:
        (backend,) = load_backends([{"BACKEND": CONCRETE}], base=AbstractFakeBackend)

        assert type(backend) is ConcreteFakeBackend
        assert backend.run() == "ok"

    def test_load_names_the_abstract_root_in_its_log(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="next.backends"):
            assert load_backends([{"BACKEND": MISSING}], base=AbstractFakeBackend) == []

        assert "error resolving AbstractFakeBackend from config" in caplog.text


class TestConfigSelection:
    """The bound settings key names one entry, whatever shape the family uses."""

    def test_dict_setting_is_used_as_the_entry(self) -> None:
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            assert type(_manager().get()) is AlphaBackend

    def test_list_setting_uses_the_first_entry(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={_LIST_SETTING: [{"BACKEND": BETA}, {"BACKEND": ALPHA}]}
        ):
            assert type(_manager(_LIST_SETTING).get()) is BetaBackend

    def test_list_setting_skips_entries_that_are_not_mappings(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={_LIST_SETTING: ["not-an-entry", {"BACKEND": ALPHA}]}
        ):
            assert type(_manager(_LIST_SETTING).get()) is AlphaBackend

    def test_backend_keeps_the_selected_entry(self) -> None:
        entry = {"BACKEND": ALPHA, "OPTIONS": {"a": 1}}
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: [entry]}):
            assert _manager(_LIST_SETTING).get().config == entry

    def test_empty_list_setting_without_default_raises(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={_LIST_SETTING: []}),
            pytest.raises(ImproperlyConfigured, match="under BACKEND"),
        ):
            _manager(_LIST_SETTING).get()

    def test_empty_list_setting_falls_back_to_the_default(self) -> None:
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: []}):
            manager = _manager(_LIST_SETTING, default=ALPHA)
            assert type(manager.get()) is AlphaBackend

    def test_unknown_settings_key_without_default_raises(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="under BACKEND"):
            _manager(_UNKNOWN_SETTING).get()

    def test_unknown_settings_key_falls_back_to_the_default(self) -> None:
        manager = _manager(_UNKNOWN_SETTING, default=BETA)
        assert type(manager.get()) is BetaBackend


class TestFailuresPropagate:
    """A single-backend family has no fallback, so a bad entry raises out of get()."""

    def test_unimportable_backend_escapes(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": MISSING}}),
            pytest.raises(ImportError),
        ):
            _manager().get()

    def test_backend_outside_the_family_escapes(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": FOREIGN}}),
            pytest.raises(ImproperlyConfigured, match="is not a FakeBackend subclass"),
        ):
            _manager().get()


class TestCachingAndReset:
    """The backend is built on first use and rebuilt only after invalidation."""

    def test_repeated_get_returns_the_cached_backend(self) -> None:
        CountingBackend.instances = 0
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": COUNTING}}):
            manager = _manager()
            first = manager.get()

            assert manager.get() is first
            assert CountingBackend.instances == 1

    def test_settings_are_not_read_before_the_first_get(self) -> None:
        CountingBackend.instances = 0
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": COUNTING}}):
            _manager()

            assert CountingBackend.instances == 0

    def test_reset_only_invalidates(self) -> None:
        CountingBackend.instances = 0
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": COUNTING}}):
            manager = _manager()
            manager.get()

            manager.reset()

            # invalidation costs no instantiation, the rebuild waits for get()
            assert CountingBackend.instances == 1
            manager.get()
            assert CountingBackend.instances == 2

    def test_get_after_reset_rereads_the_setting(self) -> None:
        manager = _manager()
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            assert type(manager.get()) is AlphaBackend

        manager.reset()
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": BETA}}):
            assert type(manager.get()) is BetaBackend

    def test_without_reset_a_changed_setting_is_ignored(self) -> None:
        manager = _manager()
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            manager.get()

        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": BETA}}):
            assert type(manager.get()) is AlphaBackend

    def test_reset_before_any_get_is_harmless(self) -> None:
        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            manager = _manager()
            manager.reset()

            assert type(manager.get()) is AlphaBackend


@pytest.mark.parametrize(("setting", "shape"), _FAMILY_SHAPES)
class TestFamilyShapes:
    """Every single-backend family gets the same lifecycle, whatever its shape."""

    def test_get_builds_the_named_backend_once(self, setting, shape) -> None:
        CountingBackend.instances = 0
        with override_settings(NEXT_FRAMEWORK={setting: shape({"BACKEND": COUNTING})}):
            manager = _manager(setting)
            first = manager.get()

            assert manager.get() is first
            assert CountingBackend.instances == 1

    def test_reset_forces_reinstantiation(self, setting, shape) -> None:
        with override_settings(NEXT_FRAMEWORK={setting: shape({"BACKEND": ALPHA})}):
            manager = _manager(setting)
            first = manager.get()

            manager.reset()

            assert manager.get() is not first

    def test_configured_entry_reaches_the_backend(self, setting, shape) -> None:
        entry = {"BACKEND": ALPHA, "OPTIONS": {"flag": True}}
        with override_settings(NEXT_FRAMEWORK={setting: shape(entry)}):
            assert _manager(setting).get().config == entry

    def test_entry_without_a_dotted_path_is_refused(self, setting, shape) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={setting: shape({"BACKEND": None})}),
            pytest.raises(ImproperlyConfigured, match="under BACKEND"),
        ):
            _manager(setting).get()
