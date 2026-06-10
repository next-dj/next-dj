import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from django import forms as django_forms
from django.http import HttpRequest

from next.forms import (
    ActionRegistration,
    Form,
    ModelForm,
    reset_form_registration_state,
)
from next.forms.autodiscover import _discovered
from next.forms.base import (
    _FRAMEWORK_ROOT,
    _auto_register_form_class,
    _find_definition_frame,
    _is_framework_file,
    _record_invalid_meta_scope,
)
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics


class TestAutoRegistration:
    """__init_subclass__ hook registers form classes automatically."""

    def test_form_subclass_registers_in_shared_scope(self, settings, tmp_path) -> None:
        """Form in a non-anchor file registers as scope='shared'."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class SharedRegistrationForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        meta = backend.get_meta("shared_registration_form")
        assert meta is not None
        assert meta["scope"] == "shared"

    def test_form_subclass_registers_in_page_scope(self, settings, tmp_path) -> None:
        """Form declared in page.py registers as scope='page'."""
        settings.BASE_DIR = tmp_path
        page_dir = tmp_path / "myapp"
        page_dir.mkdir()
        fake_path = str(page_dir / "page.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class PageScopeForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        meta = backend.get_meta("page_scope_form", fake_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_form_subclass_registers_in_component_scope(
        self, settings, tmp_path
    ) -> None:
        """Form declared in component.py registers as scope='page'."""
        settings.BASE_DIR = tmp_path
        comp_dir = tmp_path / "myapp"
        comp_dir.mkdir()
        fake_path = str(comp_dir / "component.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class ComponentScopeForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        meta = backend.get_meta("component_scope_form", fake_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_meta_scope_page_override(self, settings, tmp_path) -> None:
        """Meta.scope='page' overrides the filename-based detection."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        fake_path = str(app_dir / "other.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class MetaPageForm(Form):
                title = django_forms.CharField()

                class Meta:
                    scope = "page"

        backend = form_action_manager.default_backend
        meta = backend.get_meta("meta_page_form", fake_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_meta_scope_shared_override(self, settings, tmp_path) -> None:
        """Meta.scope='shared' overrides the filename-based detection even for page.py."""
        settings.BASE_DIR = tmp_path
        page_dir = tmp_path / "myapp"
        page_dir.mkdir()
        fake_path = str(page_dir / "page.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class MetaSharedForm(Form):
                title = django_forms.CharField()

                class Meta:
                    scope = "shared"

        backend = form_action_manager.default_backend
        meta = backend.get_meta("meta_shared_form")
        assert meta is not None
        assert meta["scope"] == "shared"

    def test_meta_scope_invalid_records_error(self, settings, tmp_path) -> None:
        """Invalid Meta.scope is recorded as a diagnostic without registering."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        registration_diagnostics.clear()
        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class BadMetaScopeForm(Form):
                title = django_forms.CharField()

                class Meta:
                    scope = "invalid_scope"

        assert any(
            "BadMetaScopeForm" in name
            for name, _ in registration_diagnostics.invalid_meta_scope
        )
        backend = form_action_manager.default_backend
        meta = backend.get_meta("bad_meta_scope_form")
        assert meta is None

    def test_outside_base_dir_records_warning(self, settings, tmp_path) -> None:
        """Form outside BASE_DIR is recorded as a diagnostic without registering."""
        settings.BASE_DIR = tmp_path / "project_root"
        outside = tmp_path / "outside"
        outside.mkdir()
        fake_path = str(outside / "forms.py")
        Path(fake_path).write_text("")

        registration_diagnostics.clear()
        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class OutsideForm(Form):
                title = django_forms.CharField()

        assert any(
            "OutsideForm" in name
            for name, _ in registration_diagnostics.outside_base_dir
        )
        backend = form_action_manager.default_backend
        assert backend.get_meta("outside_form") is None

    def test_virtual_path_skipped(self) -> None:
        """Files with paths starting with '<' (interactive/virtual) are skipped."""
        with patch("next.forms.base._find_definition_frame", return_value="<stdin>"):

            class VirtualForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        assert backend.get_meta("virtual_form") is None

    def test_empty_path_skipped(self) -> None:
        """Empty file path from _find_definition_frame is skipped."""
        with patch("next.forms.base._find_definition_frame", return_value=""):

            class EmptyPathForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        assert backend.get_meta("empty_path_form") is None

    def test_framework_file_skipped(self) -> None:
        """Forms inside the next framework package itself are not registered."""
        framework_path = str(_FRAMEWORK_ROOT / "forms" / "base.py")

        with patch(
            "next.forms.base._find_definition_frame", return_value=framework_path
        ):

            class FrameworkInternalForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        assert backend.get_meta("framework_internal_form") is None

    def test_duplicate_name_same_scope_records_collision(
        self, settings, tmp_path
    ) -> None:
        """Two registrations of the same name with different handlers produce a collision."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        registration_diagnostics.action_collisions.clear()

        def handler_v1() -> None:
            pass

        def handler_v2() -> None:
            pass

        handler_v1.__qualname__ = "collision_v1"
        handler_v2.__qualname__ = "collision_v2"

        form_action_manager.register_action(
            ActionRegistration(
                name="dup_test",
                file_path=fake_path,
                scope="shared",
                handler=handler_v1,
            )
        )
        form_action_manager.register_action(
            ActionRegistration(
                name="dup_test",
                file_path=fake_path,
                scope="shared",
                handler=handler_v2,
            )
        )

        assert len(registration_diagnostics.action_collisions) >= 1

    def test_no_base_dir_still_registers(self, settings, tmp_path) -> None:
        """When BASE_DIR is not set, forms register without location check."""
        settings.BASE_DIR = None
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class NoDirForm(Form):
                title = django_forms.CharField()

        backend = form_action_manager.default_backend
        meta = backend.get_meta("no_dir_form")
        assert meta is not None


class TestFindDefinitionFrame:
    """_find_definition_frame walks the call stack to find the definition site."""

    def test_returns_string(self) -> None:
        """_find_definition_frame returns a string (the caller's filename)."""
        result = _find_definition_frame()
        assert isinstance(result, str)

    def test_does_not_return_framework_file(self) -> None:
        """The returned path is not the framework's own base.py."""
        result = _find_definition_frame()
        if result:
            assert not _is_framework_file(result)

    def test_returns_empty_when_stack_exhausted(self) -> None:
        """When sys._getframe raises ValueError, _find_definition_frame returns ''."""
        framework_file = str(_FRAMEWORK_ROOT / "forms" / "fake.py")

        call_count = {"n": 0}

        def mock_getframe(depth: int) -> object:
            call_count["n"] += 1
            if call_count["n"] > 3:
                msg = "call stack is not deep enough"
                raise ValueError(msg)
            frame = MagicMock()
            frame.f_code.co_filename = framework_file
            return frame

        with patch.object(sys, "_getframe", side_effect=mock_getframe):
            result = _find_definition_frame()

        assert result == ""


class TestModelFormAutoRegistration:
    """BaseModelForm.__init_subclass__ hook registers ModelForm subclasses."""

    def test_model_form_subclass_registers_in_shared_scope(
        self, settings, tmp_path
    ) -> None:
        """ModelForm in a non-anchor file registers as scope='shared'."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class AuthorModelForm(ModelForm):
                class Meta:
                    model = MagicMock()
                    fields = "__all__"

        backend = form_action_manager.default_backend
        meta = backend.get_meta("author_model_form")
        assert meta is not None
        assert meta["scope"] == "shared"

    def test_model_form_subclass_registers_in_page_scope(
        self, settings, tmp_path
    ) -> None:
        """ModelForm in page.py registers as scope='page'."""
        settings.BASE_DIR = tmp_path
        page_dir = tmp_path / "myapp"
        page_dir.mkdir()
        fake_path = str(page_dir / "page.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class PageModelForm(ModelForm):
                class Meta:
                    model = MagicMock()
                    fields = "__all__"

        backend = form_action_manager.default_backend
        meta = backend.get_meta("page_model_form", fake_path)
        assert meta is not None
        assert meta["scope"] == "page"

    def test_diagnostics_clear_empties_both_lists(self) -> None:
        """registration_diagnostics.clear empties both tracking lists."""
        registration_diagnostics.outside_base_dir.append(("FakeOuter", "/fake/path.py"))
        registration_diagnostics.invalid_meta_scope.append(("FakeInvalid", "bad_scope"))
        registration_diagnostics.clear()
        assert registration_diagnostics.outside_base_dir == []
        assert registration_diagnostics.invalid_meta_scope == []


class TestAutoRegisterFormClassDirectly:
    """_auto_register_form_class: direct call covering edge-case branches."""

    def test_skips_when_no_base_dir_and_no_package(self, settings, tmp_path) -> None:
        """Form at a standalone path (no __init__.py) registers with stem as scope_key."""
        settings.BASE_DIR = None
        fake_path = str(tmp_path / "standalone_forms.py")
        Path(fake_path).write_text("")

        class DirectForm(Form):
            title = django_forms.CharField()

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):
            _auto_register_form_class(DirectForm)

        backend = form_action_manager.default_backend
        assert backend.get_meta("direct_form") is not None


class TestRecordInvalidMetaScope:
    """_record_invalid_meta_scope appends (qualname, bad_value) pairs."""

    def test_appends_entry(self) -> None:
        """Calling _record_invalid_meta_scope adds an entry to the list."""
        registration_diagnostics.clear()

        class FakeForm:
            __qualname__ = "FakeForm"

        _record_invalid_meta_scope(FakeForm, "notvalid")
        assert ("FakeForm", "notvalid") in registration_diagnostics.invalid_meta_scope
        registration_diagnostics.clear()


class TestBaseFormOnValid:
    """BaseForm.on_valid and BaseModelForm.on_valid default implementations."""

    def test_base_form_on_valid_redirects_to_origin(self) -> None:
        """BaseForm.on_valid calls redirect_to_origin and returns 302."""

        class BasicForm(Form):
            name = django_forms.CharField()

        form = BasicForm(data={"name": "Alice"})
        assert form.is_valid()

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_next_form_origin": "/origin/"}
        response = form.on_valid(request)
        assert response.status_code == 302
        assert response.url == "/origin/"

    def test_base_model_form_on_valid_saves_and_redirects(self) -> None:
        """BaseModelForm.on_valid calls self.save() and redirects to origin."""
        mock_model = MagicMock()
        mock_model._meta = MagicMock()
        mock_model._meta.get_fields.return_value = []

        class SimpleModelForm(ModelForm):
            name = django_forms.CharField()

            class Meta:
                model = mock_model
                fields = ["name"]  # noqa: RUF012

        instance = MagicMock()
        instance._meta = MagicMock()
        instance._meta.model = mock_model

        form = SimpleModelForm(data={"name": "Bob"}, instance=instance)
        form.is_valid()

        request = HttpRequest()
        request.method = "POST"
        request.POST = {"_next_form_origin": "/model-origin/"}

        response = form.on_valid(request)
        assert response.status_code == 302
        assert response.url == "/model-origin/"
        instance.save.assert_called_once()


class TestAbstractForms:
    """Meta.abstract opts a base form class out of auto-registration."""

    def test_abstract_meta_form_skips_registration(self) -> None:
        """A form with Meta.abstract = True is not auto-registered."""
        backend = form_action_manager.default_backend

        class AbstractIntermediateForm(Form):
            class Meta:
                abstract = True

        assert backend.get_meta("abstract_intermediate_form") is None


class TestResetFormRegistrationState:
    """reset_form_registration_state clears every registry and warning buffer."""

    def test_reset_clears_all_buffers(self) -> None:
        """The aggregate reset empties the registry and every tracking list."""
        backend = form_action_manager.default_backend
        backend.register_action(
            ActionRegistration(
                name="reset_probe",
                file_path="/x/page.py",
                scope="page",
                handler=lambda _request: None,
            )
        )
        registration_diagnostics.outside_base_dir.append(("Probe", "/x/forms.py"))
        registration_diagnostics.action_applied_to_class.append("Probe")
        _discovered.add("probe.forms")

        reset_form_registration_state()

        assert backend._registry == {}
        assert backend._name_index == {}
        assert registration_diagnostics.outside_base_dir == []
        assert registration_diagnostics.action_applied_to_class == []
        assert _discovered == set()
