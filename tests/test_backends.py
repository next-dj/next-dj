import logging

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.dispatch import Signal
from django.test import override_settings
from django.urls.resolvers import URLResolver

from next.backends import (
    BackendListManager,
    SingleBackendManager,
    backend_entries,
    load_backends,
    resolve_backend_class,
    resolve_setting_class,
)
from next.conf import next_framework_settings
from next.conf.frozen import FrozenDict, FrozenList
from next.errors import (
    BackendImportError,
    BackendNotSubclassError,
    SettingImportError,
    SettingNotSubclassError,
)
from next.urls.resolver import TrieURLResolver
from tests.support.backends import (
    ABSTRACT,
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
from tests.support.races import LockWonByAnotherThread


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
        klass = resolve_backend_class(
            {"BACKEND": ALPHA}, base=FakeBackend, default=BETA
        )
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
        # a constructor failing for anything but its own
        # config is a bug rather than an entry to skip
        with pytest.raises(error, match="boom"):
            load_backends(
                [{"BACKEND": RAISING, "ERROR": kind}, {"BACKEND": ALPHA}],
                base=FakeBackend,
            )

    def test_unexpected_error_from_construction_escapes(self) -> None:
        with pytest.raises(KeyError, match="ERROR"):
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

    def test_resolve_refuses_the_abstract_root_itself(self) -> None:
        with pytest.raises(ImproperlyConfigured, match="is abstract"):
            resolve_backend_class({"BACKEND": ABSTRACT}, base=AbstractFakeBackend)

    def test_load_skips_an_entry_naming_the_abstract_root(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="next.backends"):
            assert (
                load_backends([{"BACKEND": ABSTRACT}], base=AbstractFakeBackend) == []
            )

        assert "is abstract" in caplog.text

    def test_load_names_the_abstract_root_in_its_log(self, caplog) -> None:
        with caplog.at_level(logging.ERROR, logger="next.backends"):
            assert load_backends([{"BACKEND": MISSING}], base=AbstractFakeBackend) == []

        assert "error resolving AbstractFakeBackend from config" in caplog.text


class TestBackendEntries:
    """backend_entries reads one list-valued settings key defensively."""

    def test_returns_the_dict_entries(self) -> None:
        entries = [{"BACKEND": ALPHA}, {"BACKEND": BETA}]
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: entries}):
            assert backend_entries(_LIST_SETTING) == entries

    def test_non_dict_entries_are_filtered(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={_LIST_SETTING: ["not-an-entry", {"BACKEND": ALPHA}, 42]}
        ):
            assert backend_entries(_LIST_SETTING) == [{"BACKEND": ALPHA}]

    def test_non_list_value_returns_no_entries(self) -> None:
        # FORM_WIZARD_BACKEND merges to a dict, never a list of entries.
        assert backend_entries(_DICT_SETTING) == []

    def test_missing_key_returns_no_entries(self) -> None:
        assert backend_entries(_UNKNOWN_SETTING) == []


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

    def test_unimportable_backend_names_the_settings_key(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": MISSING}}),
            pytest.raises(
                ImproperlyConfigured,
                match=rf"NEXT_FRAMEWORK\['{_DICT_SETTING}'\].*cannot be imported",
            ),
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


class TestFrozenMergedValuesAreReadUnchanged:
    """The loaders read the frozen merged settings through the same guards."""

    def test_backend_entries_keeps_its_list_and_dict_guards(self) -> None:
        entries = [{"BACKEND": ALPHA}, "not-an-entry"]
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: entries}):
            selected = backend_entries(_LIST_SETTING)
            assert isinstance(next_framework_settings.PARTIAL_BACKENDS, FrozenList)
            assert selected == [{"BACKEND": ALPHA}]
            assert isinstance(selected[0], FrozenDict)

    @pytest.mark.parametrize(("setting", "shape"), _FAMILY_SHAPES)
    def test_selected_entry_stays_frozen_all_the_way_to_the_backend(
        self, setting, shape
    ) -> None:
        entry = {"BACKEND": ALPHA, "OPTIONS": {"flag": True}}
        with override_settings(NEXT_FRAMEWORK={setting: shape(entry)}):
            config = _manager(setting).get().config
            assert config == entry
            assert isinstance(config, FrozenDict)
            with pytest.raises(TypeError, match="immutable"):
                config["OPTIONS"]["flag"] = False


class TestSingleBackendManagerSignal:
    """The one-backend families announce their load the way the list ones do."""

    def test_the_signal_fires_on_the_build_rather_than_on_the_binding(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)
        entry = {"BACKEND": ALPHA, "OPTIONS": {"a": 1}}

        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: entry}):
            manager = SingleBackendManager(
                _DICT_SETTING, base=FakeBackend, signal=signal
            )
            assert recorder.calls == []
            backend = manager.get()

        assert recorder.calls == [(AlphaBackend, entry, backend)]

    def test_a_cached_backend_announces_itself_once(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            manager = SingleBackendManager(
                _DICT_SETTING, base=FakeBackend, signal=signal
            )
            manager.get()
            manager.get()

        assert len(recorder.calls) == 1

    def test_a_rebuild_after_a_reset_announces_the_new_backend(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            manager = SingleBackendManager(
                _DICT_SETTING, base=FakeBackend, signal=signal
            )
            first = manager.get()
            manager.reset()
            second = manager.get()

        assert [call[2] for call in recorder.calls] == [first, second]

    def test_the_signal_carries_a_config_copy(self) -> None:
        """A receiver that edits what it was handed may not reach the backend."""
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            manager = SingleBackendManager(
                _DICT_SETTING, base=FakeBackend, signal=signal
            )
            backend = manager.get()

        assert recorder.calls[0][1] == {"BACKEND": ALPHA}
        assert recorder.calls[0][1] is not backend.config

    def test_a_family_without_a_signal_announces_nothing(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        with override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": ALPHA}}):
            _manager().get()

        assert recorder.calls == []

    def test_a_misconfigured_entry_announces_nothing(self) -> None:
        signal = Signal()
        recorder = _Recorder()
        signal.connect(recorder, weak=False)

        with (
            override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": MISSING}}),
            pytest.raises(BackendImportError),
        ):
            SingleBackendManager(_DICT_SETTING, base=FakeBackend, signal=signal).get()

        assert recorder.calls == []


class _CountingListManager(BackendListManager[FakeBackend]):
    """Family manager over one list-valued key, counting its loads."""

    def __init__(self, *, retry_when_empty: bool = False) -> None:
        super().__init__()
        self.loads = 0
        self._retry_when_empty = retry_when_empty

    def reload(self) -> None:
        """Read the configured entries and record the load."""
        self.loads += 1
        self._backends = load_backends(backend_entries(_LIST_SETTING), base=FakeBackend)
        self._mark_loaded(retry_when_empty=self._retry_when_empty)


class TestBackendListManager:
    """The shared lazy list loads once and rereads only where a family asks it to."""

    def test_the_first_access_reads_the_settings(self) -> None:
        manager = _CountingListManager()
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: [{"BACKEND": ALPHA}]}):
            manager._ensure_backends()
            manager._ensure_backends()

        assert manager.loads == 1
        assert [type(b) for b in manager._backends] == [AlphaBackend]

    def test_an_explicit_list_skips_the_settings_read(self) -> None:
        manager = _CountingListManager()
        manager._backends = [AlphaBackend({})]
        manager._loaded = True

        manager._ensure_backends()

        assert manager.loads == 0

    def test_an_empty_load_is_a_result_by_default(self) -> None:
        manager = _CountingListManager()
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: []}):
            manager._ensure_backends()
            manager._ensure_backends()

        assert manager.loads == 1

    def test_an_empty_load_is_reread_where_the_family_calls_it_broken(self) -> None:
        manager = _CountingListManager(retry_when_empty=True)
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: []}):
            manager._ensure_backends()
            manager._ensure_backends()

        assert manager.loads == 2

    def test_a_loaded_list_is_kept_even_where_an_empty_one_is_broken(self) -> None:
        manager = _CountingListManager(retry_when_empty=True)
        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: [{"BACKEND": ALPHA}]}):
            manager._ensure_backends()
            manager._ensure_backends()

        assert manager.loads == 1

    def test_a_thread_that_lost_the_race_reads_the_list_the_winner_loaded(self) -> None:
        """The check under the lock is what keeps two threads from both loading."""
        manager = _CountingListManager()
        lock = LockWonByAnotherThread(manager, "_loaded")
        manager._lock = lock

        with override_settings(NEXT_FRAMEWORK={_LIST_SETTING: [{"BACKEND": ALPHA}]}):
            manager._ensure_backends()

        assert lock.entered == 1
        assert manager.loads == 0


def _resolve_url_resolver_setting() -> type[URLResolver]:
    """Read one dotted-path settings key through the shared resolver."""
    return resolve_setting_class(
        "URL_RESOLVER",
        base=URLResolver,
        shipped=TrieURLResolver,
        base_path="django.urls.resolvers.URLResolver",
    )


class TestBackendErrorAttributes:
    """Which attributes an error carries follows from its class, not its caller."""

    def test_an_entry_that_does_not_import_carries_only_its_key(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={_DICT_SETTING: {"BACKEND": MISSING}}),
            pytest.raises(BackendImportError) as caught,
        ):
            _manager().get()

        assert caught.value.setting == _DICT_SETTING
        assert not hasattr(caught.value, "dotted")

    def test_a_dotted_path_setting_that_does_not_import_carries_the_path(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"URL_RESOLVER": MISSING}),
            pytest.raises(SettingImportError) as caught,
        ):
            _resolve_url_resolver_setting()

        assert caught.value.setting == "URL_RESOLVER"
        assert caught.value.dotted == MISSING

    def test_an_entry_outside_the_family_names_no_settings_key(self) -> None:
        with pytest.raises(BackendNotSubclassError) as caught:
            resolve_backend_class({"BACKEND": FOREIGN}, base=FakeBackend)

        assert caught.value.dotted == FOREIGN
        assert caught.value.base_name == "FakeBackend"
        assert not hasattr(caught.value, "setting")

    def test_a_dotted_path_setting_outside_the_family_names_its_key(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"URL_RESOLVER": FOREIGN}),
            pytest.raises(SettingNotSubclassError) as caught,
        ):
            _resolve_url_resolver_setting()

        assert caught.value.setting == "URL_RESOLVER"
        assert caught.value.dotted == FOREIGN
        assert caught.value.base_name == "django.urls.resolvers.URLResolver"
