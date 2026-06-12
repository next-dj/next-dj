import copy
from unittest.mock import MagicMock, patch

import pytest
from django import forms as django_forms
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404, HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import (
    NoReverseMatch,
    URLPattern,
    clear_script_prefix,
    set_script_prefix,
)

from next.forms import (
    ActionOutcome,
    ActionOutcomeKind,
    ActionRegistration,
    FormActionBackend,
    FormActionNotFound,
    RegistryFormActionBackend,
)
from next.forms.backends import (
    FormActionFactory,
    file_to_dotted_module,
    scope_key_for,
)
from next.forms.manager import FormActionManager, form_action_manager


_FAKE_FILE = "/fake/myapp/forms.py"
_FAKE_FILE_PAGE = "/fake/myapp/page.py"


class TestFormActionNotFound:
    """FormActionNotFound carries the failing name and lookup context."""

    def test_is_a_lookup_error(self) -> None:
        """The exception subclasses LookupError, not KeyError."""
        exc = FormActionNotFound("Unknown form action 'x'.", name="x")
        assert isinstance(exc, LookupError)
        assert not isinstance(exc, KeyError)

    def test_explicit_message_is_kept_verbatim(self) -> None:
        """str() returns an explicitly passed message unchanged."""
        exc = FormActionNotFound("Unknown form action 'x'.", name="x")
        assert str(exc) == "Unknown form action 'x'."

    def test_default_context_fields(self) -> None:
        """page_path defaults to None and suggestions to an empty tuple."""
        exc = FormActionNotFound(name="missing_action")
        assert exc.name == "missing_action"
        assert exc.page_path is None
        assert exc.suggestions == ()
        assert exc.registry_empty is False

    def test_context_fields_are_stored(self) -> None:
        """name, page_path, and suggestions are exposed as attributes."""
        exc = FormActionNotFound(
            name="missing_action",
            page_path="/app/pages/page.py",
            suggestions=["missing_actions"],
        )
        assert exc.name == "missing_action"
        assert exc.page_path == "/app/pages/page.py"
        assert exc.suggestions == ("missing_actions",)

    def test_catchable_by_type(self) -> None:
        """The exception can be caught by its own type."""
        with pytest.raises(FormActionNotFound):
            raise FormActionNotFound(name="missing_action")

    def test_reduce_round_trip_keeps_message(self) -> None:
        """Replaying args, as pickle and deepcopy do, keeps the message."""
        exc = FormActionNotFound(
            name="vote",
            page_path="/app/page.py",
            suggestions=("veto",),
        )
        revived = copy.deepcopy(exc)
        assert isinstance(revived, FormActionNotFound)
        assert str(revived) == str(exc)
        assert str(FormActionNotFound(*exc.args)) == str(exc)

    @pytest.mark.parametrize(
        ("kwargs", "expected"),
        [
            pytest.param(
                {"name": "vote", "page_path": "/app/page.py"},
                (
                    "Unknown form action 'vote'. Searched page scope for "
                    "/app/page.py and the shared registry."
                ),
                id="page-path-without-suggestions",
            ),
            pytest.param(
                {"name": "vote"},
                (
                    "Unknown form action 'vote'. "
                    "Searched the shared registry (no page scope)."
                ),
                id="no-page-path",
            ),
            pytest.param(
                {"name": "vot", "suggestions": ("vote", "veto")},
                (
                    "Unknown form action 'vot'. "
                    "Searched the shared registry (no page scope). "
                    "Closest matches: 'vote', 'veto'."
                ),
                id="suggestions-appended",
            ),
            pytest.param(
                {"name": "vote", "registry_empty": True},
                (
                    "Unknown form action 'vote'. "
                    "Searched the shared registry (no page scope). "
                    "No form actions are registered. Check that the "
                    "declaring module is imported. Autodiscover imports "
                    "each app's forms.py when FORM_AUTODISCOVER is enabled."
                ),
                id="empty-registry-diagnosis",
            ),
        ],
    )
    def test_composed_message(self, kwargs: dict[str, object], expected: str) -> None:
        """The message renders the lookup context, hints, and registry state."""
        assert str(FormActionNotFound(**kwargs)) == expected


class TestFormActionManager:
    """FormActionManager: get_action_url, default_backend, __iter__."""

    def test_get_action_url_returns_url(self) -> None:
        """Return URL for known action."""
        url = form_action_manager.get_action_url("simple_form")
        assert url != ""
        assert "_next/form/" in url

    def test_get_action_url_returns_url_for_form_less(self) -> None:
        """Return URL for form-less action."""
        url = form_action_manager.get_action_url("test_no_form")
        assert "_next/form/" in url

    def test_get_action_url_raises_for_unknown_action(self) -> None:
        """Raise FormActionNotFound for unknown action name."""
        with pytest.raises(FormActionNotFound, match="Unknown form action") as excinfo:
            form_action_manager.get_action_url("nonexistent_action_xyz")
        assert excinfo.value.name == "nonexistent_action_xyz"
        assert excinfo.value.page_path is None
        assert excinfo.value.suggestions == ()

    def test_get_action_meta_returns_meta_with_uid(self) -> None:
        """Return the registry meta whose uid also keys the action URL."""
        meta = form_action_manager.get_action_meta("simple_form")
        assert meta is not None
        assert meta["uid"]
        assert meta["uid"] in form_action_manager.get_action_url("simple_form")

    def test_get_action_meta_returns_none_for_unknown(self) -> None:
        """Return None when no backend knows the action name."""
        assert form_action_manager.get_action_meta("nonexistent_action_xyz") is None

    def test_get_action_meta_skips_backend_without_meta(self) -> None:
        """Skip backends whose get_meta yields None and ask the next one."""

        class NoMetaBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        registry = RegistryFormActionBackend()
        registry.register_action(
            ActionRegistration(
                name="meta_proxy_action",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        manager = FormActionManager(backends=[NoMetaBackend(), registry])
        meta = manager.get_action_meta("meta_proxy_action")
        assert meta is not None
        assert meta["uid"]

    def test_require_action_meta_returns_meta(self) -> None:
        """Return the same meta as get_action_meta for a known name."""
        meta = form_action_manager.require_action_meta("simple_form")
        assert meta["name"] == "simple_form"
        assert meta["uid"]

    def test_require_action_meta_raises_with_suggestions(self) -> None:
        """Raise FormActionNotFound carrying close matches for a near miss."""
        with pytest.raises(FormActionNotFound, match="Closest matches") as excinfo:
            form_action_manager.require_action_meta("simple_frm")
        assert "simple_form" in excinfo.value.suggestions
        assert excinfo.value.registry_empty is False

    def test_require_action_meta_reports_empty_registry(self) -> None:
        """An empty backend list surfaces the registry-empty diagnosis."""
        manager = FormActionManager(backends=[RegistryFormActionBackend()])
        with pytest.raises(FormActionNotFound, match="No form actions") as excinfo:
            manager.require_action_meta("anything")
        assert excinfo.value.registry_empty is True
        assert excinfo.value.suggestions == ()

    def test_default_backend_is_first_backend(self) -> None:
        """Default backend is the first in the list."""
        assert form_action_manager.default_backend is form_action_manager._backends[0]

    def test_iter_yields_url_patterns(self) -> None:
        """Iteration yields URL patterns from backends."""
        patterns = list(form_action_manager)
        assert isinstance(patterns, list)
        assert len(patterns) >= 1
        assert any("_next/form" in str(p.pattern) for p in patterns)

    def test_no_backends_configured_raises_improperly_configured(
        self, settings
    ) -> None:
        """Empty FORM_ACTION_BACKENDS raises ImproperlyConfigured."""
        settings.NEXT_FRAMEWORK = {"FORM_ACTION_BACKENDS": []}
        manager = FormActionManager()
        with pytest.raises(ImproperlyConfigured, match="No form action backends"):
            manager.register_action(
                ActionRegistration(
                    name="orphan_action",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    handler=lambda: None,
                )
            )
        with pytest.raises(ImproperlyConfigured, match="No form action backends"):
            _ = manager.default_backend


class TestRegistryFormActionBackend:
    """RegistryFormActionBackend registers, looks up, and routes actions."""

    def test_get_action_url_raises_for_unknown(self) -> None:
        """Backend raises FormActionNotFound for unknown action."""
        backend = form_action_manager.default_backend
        assert isinstance(backend, RegistryFormActionBackend)
        with pytest.raises(FormActionNotFound, match="Unknown form action"):
            backend.get_action_url("nonexistent_xyz")

    def test_generate_urls_empty_when_no_actions(self) -> None:
        """Empty backend yields no URL patterns."""
        empty_backend = RegistryFormActionBackend()
        assert empty_backend.generate_urls() == []

    def test_register_action_stores_handler(self) -> None:
        """Handler is stored under (scope_key, name) key."""
        backend = RegistryFormActionBackend()

        def my_handler() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="my_action",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=my_handler,
            )
        )
        meta = backend.get_meta("my_action")
        assert meta is not None
        assert meta["handler"] is my_handler
        assert meta["form_class"] is None

    def test_register_action_stores_form_class(self) -> None:
        """form_class is stored and handler is None when only form_class given."""
        backend = RegistryFormActionBackend()

        class MyForm(django_forms.Form):
            name = django_forms.CharField()

        backend.register_action(
            ActionRegistration(
                name="my_form_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=MyForm,
            )
        )
        meta = backend.get_meta("my_form_action")
        assert meta is not None
        assert meta["form_class"] is MyForm
        assert meta["handler"] is None

    def test_register_action_stores_both_form_class_and_handler(self) -> None:
        """When both form_class and handler are given, both are stored in meta."""
        backend = RegistryFormActionBackend()

        class DualForm(django_forms.Form):
            name = django_forms.CharField()

        def dual_handler(request):
            pass

        backend.register_action(
            ActionRegistration(
                name="dual_action",
                file_path=_FAKE_FILE,
                scope="shared",
                form_class=DualForm,
                handler=dual_handler,
            )
        )
        meta = backend.get_meta("dual_action")
        assert meta is not None
        assert meta["form_class"] is DualForm
        assert meta["handler"] is dual_handler

    def test_register_action_page_scope_uses_file_path_as_scope_key(
        self, tmp_path
    ) -> None:
        """Page-scope action stores absolute file path as scope_key."""
        backend = RegistryFormActionBackend()
        page_file = str(tmp_path / "page.py")

        def handler() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="page_action",
                file_path=page_file,
                scope="page",
                handler=handler,
            )
        )
        meta = backend.get_meta("page_action", page_file)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_register_action_raises_on_uid_collision(self) -> None:
        """Raise ImproperlyConfigured when two distinct names share a UID."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="alpha",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        first_uid = next(iter(backend._uid_to_name))
        with (
            patch(
                "next.forms.backends._make_uid_for_action",
                return_value=first_uid,
            ),
            pytest.raises(ImproperlyConfigured, match="UID collision"),
        ):
            backend.register_action(
                ActionRegistration(
                    name="beta",
                    file_path=_FAKE_FILE,
                    scope="shared",
                    handler=lambda: None,
                )
            )

    def test_register_action_reregistration_same_name_ok(self) -> None:
        """Re-registering the same name (e.g. reload) does not raise."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="alpha",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        backend.register_action(
            ActionRegistration(
                name="alpha",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        assert backend.get_meta("alpha") is not None

    def test_registry_keys_are_scope_name_tuples(self) -> None:
        """Internal registry uses (scope_key, name) tuples as keys."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="tuple_test",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=h,
            )
        )
        keys = list(backend._registry.keys())
        assert all(isinstance(k, tuple) and len(k) == 2 for k in keys)

    def test_uid_to_name_values_are_scope_name_tuples(self) -> None:
        """_uid_to_name values are (scope_key, name) tuples."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="uid_tuple_test",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=h,
            )
        )
        values = list(backend._uid_to_name.values())
        assert all(isinstance(v, tuple) and len(v) == 2 for v in values)

    def test_get_meta_with_page_path_returns_page_scoped_action(self, tmp_path) -> None:
        """get_meta with page_path finds page-scoped actions."""
        backend = RegistryFormActionBackend()
        page_path = str(tmp_path / "page.py")

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="page_meta_test",
                file_path=page_path,
                scope="page",
                handler=h,
            )
        )
        meta = backend.get_meta("page_meta_test", page_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_get_meta_without_page_path_finds_any_scope(self) -> None:
        """get_meta without page_path scans all registrations."""
        backend = RegistryFormActionBackend()

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="any_scope_test",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=h,
            )
        )
        meta = backend.get_meta("any_scope_test")
        assert meta is not None

    def test_get_meta_tolerates_dangling_name_index_entry(self) -> None:
        """A name index entry without a registry record returns None."""
        backend = RegistryFormActionBackend()
        backend._name_index["ghost"] = ("ghost_scope", "ghost")
        assert backend.get_meta("ghost") is None

    def test_get_action_url_tolerates_dangling_name_index_entry(self) -> None:
        """A name index entry without a registry record raises FormActionNotFound."""
        backend = RegistryFormActionBackend()
        backend._name_index["ghost"] = ("ghost_scope", "ghost")
        with pytest.raises(FormActionNotFound, match="Unknown form action"):
            backend.get_action_url("ghost")

    def test_dispatch_unknown_uid_raises_http404(self) -> None:
        """Dispatch with an unknown UID raises a diagnosable Http404."""
        backend = RegistryFormActionBackend()
        req = HttpRequest()
        req.method = "POST"
        with pytest.raises(Http404, match="may be stale") as excinfo:
            backend.dispatch(req, "nonexistent_uid_xyz")
        assert "nonexistent_uid_xyz" in str(excinfo.value)

    def test_clear_registry_empties_all_state(self) -> None:
        """clear_registry drops all registrations and UID mapping."""
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="to_clear",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        backend.clear_registry()
        assert backend._registry == {}
        assert backend._uid_to_name == {}


class TestNameIndexScopeFilter:
    """The name-index fallback honours registration scope for page lookups."""

    @staticmethod
    def _register(
        backend: RegistryFormActionBackend, name: str, file_path: str, scope: str
    ) -> None:
        backend.register_action(
            ActionRegistration(
                name=name,
                file_path=file_path,
                scope=scope,
                handler=lambda: None,
            )
        )

    def test_page_scoped_action_invisible_from_another_page(self, tmp_path) -> None:
        """A page-scoped name never resolves through another page's lookup."""
        backend = RegistryFormActionBackend()
        page_a = str(tmp_path / "a" / "page.py")
        page_b = str(tmp_path / "b" / "page.py")
        self._register(backend, "note_form", page_a, "page")
        assert backend.get_meta("note_form", page_b) is None
        with pytest.raises(FormActionNotFound, match="Unknown form action") as excinfo:
            backend.get_action_url("note_form", page_path=page_b)
        assert excinfo.value.name == "note_form"
        assert excinfo.value.page_path == page_b

    def test_shared_action_resolves_from_any_page(self, tmp_path) -> None:
        """A shared-scope name resolves through any page's lookup."""
        backend = RegistryFormActionBackend()
        page_b = str(tmp_path / "b" / "page.py")
        self._register(backend, "shared_form", _FAKE_FILE, "shared")
        meta = backend.get_meta("shared_form", page_b)
        assert meta is not None
        assert meta["scope"] == "shared"
        url = backend.get_action_url("shared_form", page_path=page_b)
        assert "_next/form/" in url

    def test_bare_name_still_resolves_page_scoped_action(self, tmp_path) -> None:
        """A lookup without page_path keeps the unfiltered name-index fallback."""
        backend = RegistryFormActionBackend()
        page_a = str(tmp_path / "a" / "page.py")
        self._register(backend, "note_form", page_a, "page")
        meta = backend.get_meta("note_form")
        assert meta is not None
        assert meta["scope"] == "page"
        assert "_next/form/" in backend.get_action_url("note_form")

    def test_declaring_page_resolves_its_own_action(self, tmp_path) -> None:
        """The declaring page hits the exact registry key, no fallback needed."""
        backend = RegistryFormActionBackend()
        page_a = str(tmp_path / "a" / "page.py")
        self._register(backend, "note_form", page_a, "page")
        meta = backend.get_meta("note_form", page_a)
        assert meta is not None
        assert meta["scope"] == "page"
        url = backend.get_action_url("note_form", page_path=page_a)
        assert "_next/form/" in url


class TestUnknownActionSuggestions:
    """Lookup failures carry close-match suggestions from the registry."""

    @staticmethod
    def _register(backend: RegistryFormActionBackend, name: str) -> None:
        backend.register_action(
            ActionRegistration(
                name=name,
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )

    @pytest.mark.parametrize(
        ("lookup_name", "expected_suggestions", "message_tail"),
        [
            pytest.param(
                "delete_not",
                ("delete_note",),
                "Closest matches: 'delete_note'.",
                id="near-miss-suggests-neighbour",
            ),
            pytest.param(
                "zzzzzz",
                (),
                "the shared registry (no page scope).",
                id="unrelated-name-stays-bare",
            ),
        ],
    )
    def test_backend_suggestions(
        self,
        lookup_name: str,
        expected_suggestions: tuple[str, ...],
        message_tail: str,
    ) -> None:
        """The lookup error carries close matches and renders them last."""
        backend = RegistryFormActionBackend()
        self._register(backend, "delete_note")
        with pytest.raises(FormActionNotFound) as excinfo:
            backend.get_action_url(lookup_name)
        assert excinfo.value.suggestions == expected_suggestions
        assert str(excinfo.value).endswith(message_tail)

    def test_manager_aggregates_and_dedupes_across_backends(self) -> None:
        """The manager merges backend suggestions without duplicates."""
        first = RegistryFormActionBackend()
        second = RegistryFormActionBackend()
        self._register(first, "delete_note")
        self._register(second, "delete_note")
        self._register(second, "delete_card")
        manager = FormActionManager(backends=[first, second])
        with pytest.raises(FormActionNotFound) as excinfo:
            manager.get_action_url("delete_not")
        assert excinfo.value.suggestions == ("delete_note", "delete_card")
        assert "Closest matches: 'delete_note', 'delete_card'." in str(excinfo.value)


class TestEmptyRegistryDiagnosis:
    """Lookup failures on an empty registry explain the autodiscover miss."""

    @staticmethod
    def _register(backend: RegistryFormActionBackend, name: str) -> None:
        backend.register_action(
            ActionRegistration(
                name=name,
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )

    def test_backend_empty_registry_mentions_autodiscover(self) -> None:
        """An empty backend names the import miss instead of a bare typo message."""
        backend = RegistryFormActionBackend()
        with pytest.raises(FormActionNotFound) as excinfo:
            backend.get_action_url("save_note")
        assert excinfo.value.registry_empty is True
        assert "No form actions are registered" in str(excinfo.value)
        assert "FORM_AUTODISCOVER" in str(excinfo.value)

    def test_backend_with_actions_skips_the_diagnosis(self) -> None:
        """A populated backend reports a plain unknown-action failure."""
        backend = RegistryFormActionBackend()
        self._register(backend, "delete_note")
        with pytest.raises(FormActionNotFound) as excinfo:
            backend.get_action_url("zzzzzz")
        assert excinfo.value.registry_empty is False
        assert "No form actions are registered" not in str(excinfo.value)

    def test_manager_empty_backends_mention_autodiscover(self) -> None:
        """The manager keeps the diagnosis when every backend is empty."""
        manager = FormActionManager(backends=[RegistryFormActionBackend()])
        with pytest.raises(FormActionNotFound) as excinfo:
            manager.get_action_url("save_note")
        assert excinfo.value.registry_empty is True
        assert "No form actions are registered" in str(excinfo.value)

    def test_manager_with_any_registered_action_skips_the_diagnosis(self) -> None:
        """One populated backend is enough to drop the empty-registry hint."""
        first = RegistryFormActionBackend()
        second = RegistryFormActionBackend()
        self._register(second, "delete_note")
        manager = FormActionManager(backends=[first, second])
        with pytest.raises(FormActionNotFound) as excinfo:
            manager.get_action_url("zzzzzz")
        assert excinfo.value.registry_empty is False
        assert "No form actions are registered" not in str(excinfo.value)


class TestFormActionBackendAbstract:
    """FormActionBackend base methods keep their documented defaults."""

    def test_get_meta_returns_none(self) -> None:
        """Abstract backend get_meta returns None."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        stub = StubBackend()
        assert stub.get_meta("any") is None

    def test_render_invalid_page_returns_empty(self) -> None:
        """Abstract backend render_invalid_page returns empty string."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        stub = StubBackend()
        req = HttpRequest()
        assert stub.render_invalid_page(req, "x", None, None) == ""

    def test_hooks_accept_documented_keyword_names(self) -> None:
        """The base hooks keep the plain parameter names subclasses override."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        stub = StubBackend()
        req = HttpRequest()
        assert stub.get_meta(action_name="x", page_path="/p") is None
        rendered = stub.render_invalid_page(
            request=req,
            action_name="x",
            form=None,
            page_file_path=None,
            url_kwargs=None,
        )
        assert rendered == ""

    def test_shape_response_default_envelope(self) -> None:
        """Abstract backend shape_response delegates to the default envelope."""

        class StubBackend(FormActionBackend):
            def register_action(self, *args: object, **kwargs: object) -> None:
                pass

            def get_action_url(self, action_name: str, **kwargs: object) -> str:
                return ""

            def generate_urls(self) -> list:
                return []

            def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
                return HttpResponse()

        stub = StubBackend()
        req = HttpRequest()
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.RESULT, action_name="x", raw="hi"
        )
        resp = stub.shape_response(req, outcome)
        assert resp.status_code == 200
        assert resp.content == b"hi"


class _MinimalBackend(FormActionBackend):
    """Concrete backend that keeps every FormActionBackend default."""

    def register_action(self, registration: ActionRegistration) -> None:
        pass

    def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
        return ""

    def generate_urls(self) -> list[URLPattern]:
        return []

    def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
        return HttpResponse()


class TestIterActions:
    """iter_actions exposes every owned action across backend kinds."""

    def test_abstract_default_is_empty(self) -> None:
        """A backend that never overrides iter_actions yields nothing."""
        assert list(_MinimalBackend().iter_actions()) == []

    def test_registry_backend_yields_metas_with_names(self) -> None:
        """The registry backend yields stored metas carrying action names."""
        backend = RegistryFormActionBackend()
        for name in ("first_action", "second_action"):
            backend.register_action(
                ActionRegistration(
                    name=name,
                    file_path=_FAKE_FILE,
                    scope="shared",
                    handler=lambda: None,
                )
            )
        metas = list(backend.iter_actions())
        assert [meta.get("name") for meta in metas] == [
            "first_action",
            "second_action",
        ]
        assert all(meta.get("uid") for meta in metas)

    def test_empty_registry_backend_yields_nothing(self) -> None:
        """A registry backend without registrations yields no metas."""
        assert list(RegistryFormActionBackend().iter_actions()) == []


class TestManagerBackendsAccessor:
    """FormActionManager.backends exposes backends in consultation order."""

    def test_returns_explicit_backends_in_order(self) -> None:
        """Explicitly supplied backends come back as an ordered tuple."""
        first = RegistryFormActionBackend()
        second = _MinimalBackend()
        manager = FormActionManager(backends=[first, second])
        assert manager.backends == (first, second)

    def test_loads_configuration_lazily(self, settings) -> None:
        """An unprimed manager builds its backends from settings."""
        settings.NEXT_FRAMEWORK = {
            "FORM_ACTION_BACKENDS": [
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }
        manager = FormActionManager()
        backends = manager.backends
        assert len(backends) == 1
        assert isinstance(backends[0], RegistryFormActionBackend)

    def test_empty_configuration_yields_empty_tuple(self, settings) -> None:
        """No configured backends produce an empty tuple, not an error."""
        settings.NEXT_FRAMEWORK = {"FORM_ACTION_BACKENDS": []}
        manager = FormActionManager()
        assert manager.backends == ()


class TestFormActionManagerReloadConfig:
    """`_reload_config` reads `FORM_ACTION_BACKENDS` defensively."""

    def test_non_dict_entries_are_skipped(self, settings) -> None:
        """Non-dict entries inside the list are skipped without raising."""
        settings.NEXT_FRAMEWORK = {
            "FORM_ACTION_BACKENDS": [
                "not-a-dict",
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }
        manager = FormActionManager()
        manager._reload_config()
        assert len(manager._backends) == 1
        assert isinstance(manager._backends[0], RegistryFormActionBackend)

    def test_factory_failure_is_logged_and_skipped(self, settings, caplog) -> None:
        """If a backend constructor raises ImproperlyConfigured, the entry is skipped and logged."""
        settings.NEXT_FRAMEWORK = {
            "FORM_ACTION_BACKENDS": [
                {"BACKEND": "next.forms.RegistryFormActionBackend"},
            ],
        }

        def boom(_config: dict) -> None:
            msg = "boom"
            raise ImproperlyConfigured(msg)

        with patch.object(FormActionFactory, "create_backend", side_effect=boom):
            manager = FormActionManager()
            with caplog.at_level("ERROR"):
                manager._reload_config()
        assert manager._backends == []
        assert any(
            "Error creating form-action backend" in r.message for r in caplog.records
        )


class TestFormActionFactory:
    """`FormActionFactory.create_backend` resolves dotted paths to backends."""

    def test_explicit_backend_path(self) -> None:
        """Explicit `BACKEND` path is honoured."""
        backend = FormActionFactory.create_backend(
            {"BACKEND": "next.forms.RegistryFormActionBackend"},
        )
        assert isinstance(backend, RegistryFormActionBackend)

    def test_missing_backend_key_raises_keyerror(self) -> None:
        """Configuration without `BACKEND` is the system-check's responsibility."""
        with pytest.raises(KeyError):
            FormActionFactory.create_backend({})


class TestGetActionUrlNoReverseMatchFallback:
    """get_action_url falls back to URL_NAME_FORM_ACTION when FORM_ACTION_REVERSE_NAME fails."""

    def test_fallback_when_named_url_fails(self, tmp_path) -> None:
        """When FORM_ACTION_REVERSE_NAME raises NoReverseMatch, falls back to URL_NAME_FORM_ACTION."""
        backend = RegistryFormActionBackend()
        page_path = str(tmp_path / "page.py")

        def h() -> None:
            pass

        backend.register_action(
            ActionRegistration(
                name="fallback_action",
                file_path=page_path,
                scope="page",
                handler=h,
            )
        )

        original_reverse = __import__("django.urls", fromlist=["reverse"]).reverse

        def mock_reverse(name: str, **kwargs: object) -> str:
            if name == "next:form_action":
                msg = "no such url"
                raise NoReverseMatch(msg)
            return original_reverse(name, **kwargs)

        with patch("next.forms.uid.reverse", side_effect=mock_reverse):
            url = backend.get_action_url("fallback_action", page_path=page_path)
        assert "_next/form/" in url


class TestActionUrlCache:
    """get_action_url memoises reversed URLs per uid on the backend instance."""

    @staticmethod
    def _backend_with_action() -> RegistryFormActionBackend:
        backend = RegistryFormActionBackend()
        backend.register_action(
            ActionRegistration(
                name="cached_action",
                file_path=_FAKE_FILE,
                scope="shared",
                handler=lambda: None,
            )
        )
        return backend

    def test_second_call_skips_reverse(self) -> None:
        """The second lookup is served from the cache without reversing again."""
        backend = self._backend_with_action()
        first = backend.get_action_url("cached_action")
        with patch("next.forms.backends.reverse_form_action") as mocked:
            second = backend.get_action_url("cached_action")
        assert second == first
        mocked.assert_not_called()

    def test_clear_registry_drops_url_cache(self) -> None:
        """clear_registry resets the URL cache together with the registry."""
        backend = self._backend_with_action()
        backend.get_action_url("cached_action")
        backend.clear_registry()
        assert backend._url_cache == {}

    def test_root_urlconf_change_drops_url_cache(self) -> None:
        """Overriding ROOT_URLCONF invalidates cached URLs."""
        backend = self._backend_with_action()
        backend.get_action_url("cached_action")
        assert backend._url_cache != {}
        with override_settings(ROOT_URLCONF="next.urls"):
            assert backend._url_cache == {}

    def test_unrelated_setting_change_keeps_url_cache(self) -> None:
        """Overriding an unrelated setting leaves cached URLs in place."""
        backend = self._backend_with_action()
        url = backend.get_action_url("cached_action")
        with override_settings(APPEND_SLASH=False):
            assert backend._url_cache != {}
            assert backend.get_action_url("cached_action") == url

    def test_script_prefix_is_part_of_the_cache_key(self) -> None:
        """A request-scoped script prefix never serves another prefix's URL."""
        backend = self._backend_with_action()
        bare = backend.get_action_url("cached_action")
        try:
            set_script_prefix("/mounted/")
            prefixed = backend.get_action_url("cached_action")
        finally:
            clear_script_prefix()
        assert prefixed == f"/mounted{bare}"
        assert backend.get_action_url("cached_action") == bare


class TestManagerClearRegistries:
    """FormActionManager.clear_registries calls clear_registry on backends."""

    def test_clear_registries_calls_clear_registry(self) -> None:
        """clear_registries invokes clear_registry on backends that have it."""
        mock_backend = MagicMock()
        mock_backend.clear_registry = MagicMock()

        manager = FormActionManager(backends=[mock_backend])
        manager.clear_registries()

        mock_backend.clear_registry.assert_called_once()

    def test_clear_registries_skips_backend_without_method(self) -> None:
        """Backends without clear_registry are silently skipped."""
        mock_backend = MagicMock(spec=[])

        manager = FormActionManager(backends=[mock_backend])
        manager.clear_registries()


class TestFileToDottedModule:
    """file_to_dotted_module returns dotted module path for files inside packages."""

    def test_standalone_file_returns_stem(self, tmp_path) -> None:
        """File not in a package returns just the file stem."""
        f = tmp_path / "mymodule.py"
        f.write_text("")
        assert file_to_dotted_module(str(f)) == "mymodule"

    def test_file_in_package_returns_dotted_name(self, tmp_path) -> None:
        """File inside a package includes the top-level package in the dotted name."""
        pkg = tmp_path / "myapp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        f = pkg / "forms.py"
        f.write_text("")
        result = file_to_dotted_module(str(f))
        assert result == "myapp.forms"

    def test_nested_package(self, tmp_path) -> None:
        """Deeply nested package returns the full dotted path from the top package."""
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (tmp_path / "a" / "__init__.py").write_text("")
        (deep / "__init__.py").write_text("")
        f = deep / "forms.py"
        f.write_text("")
        result = file_to_dotted_module(str(f))
        assert result == "a.b.forms"


class TestScopeKeyFor:
    """scope_key_for partitions page scope by path and shared scope by module."""

    def test_page_scope_uses_resolved_path(self, tmp_path) -> None:
        """Page scope keys are the resolved file path."""
        page_file = tmp_path / "page.py"
        page_file.write_text("")
        assert scope_key_for(str(page_file), "page") == str(page_file.resolve())

    def test_shared_scope_uses_dotted_module(self, tmp_path) -> None:
        """Shared scope keys are the dotted module of the declaring file."""
        pkg = tmp_path / "myapp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        forms_file = pkg / "forms.py"
        forms_file.write_text("")
        assert scope_key_for(str(forms_file), "shared") == "myapp.forms"


class TestSharedScopeKeysAcrossApps:
    """Same-named shared forms in different app packages stay distinct."""

    def test_same_named_forms_in_two_apps_register_separately(self, tmp_path) -> None:
        """Two shared registrations from different packages get distinct keys and UIDs."""
        backend = RegistryFormActionBackend()
        for app in ("appone", "apptwo"):
            app_dir = tmp_path / app
            app_dir.mkdir()
            (app_dir / "__init__.py").write_text("")
            forms_file = app_dir / "forms.py"
            forms_file.write_text("")
            backend.register_action(
                ActionRegistration(
                    name="contact_form",
                    file_path=str(forms_file),
                    scope="shared",
                    handler=lambda: None,
                )
            )
        assert sorted(backend._registry) == [
            ("appone.forms", "contact_form"),
            ("apptwo.forms", "contact_form"),
        ]
        uids = {meta["uid"] for meta in backend._registry.values()}
        assert len(uids) == 2
