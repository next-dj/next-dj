import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from django import forms as django_forms
from django.http import HttpRequest
from django.test import override_settings

from next.conf import next_framework_settings
from next.forms import (
    ActionRegistration,
    Form,
    ModelForm,
)
from next.forms.base import (
    _FRAMEWORK_ROOT,
    _auto_register_form_class,
    _compute_scope,
    _find_definition_frame,
    _is_framework_file,
    _is_self_registered,
    _record_invalid_meta_scope,
    _to_snake_case,
)
from next.forms.diagnostics import registration_diagnostics
from next.forms.manager import form_action_manager


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


def _exec_module_from_file(module_name: str, module_file: Path) -> None:
    spec = importlib.util.spec_from_file_location(module_name, module_file)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


class TestUserFormsPackageRegistration:
    """Forms declared in a user forms/ package register via the real frame walk."""

    def test_form_in_forms_package_module_registers(self, settings, tmp_path) -> None:
        """A Form in myapp/forms/models.py registers with its own file_path."""
        settings.BASE_DIR = tmp_path
        forms_pkg = tmp_path / "myapp" / "forms"
        forms_pkg.mkdir(parents=True)
        (tmp_path / "myapp" / "__init__.py").write_text("")
        (forms_pkg / "__init__.py").write_text("")
        module_file = forms_pkg / "models.py"
        module_file.write_text(
            "from next.forms import CharField, Form\n"
            "\n"
            "\n"
            "class PackagedModelsForm(Form):\n"
            "    title = CharField()\n"
        )

        _exec_module_from_file("user_forms_pkg_models", module_file)

        backend = form_action_manager.default_backend
        meta = backend.get_meta("packaged_models_form")
        assert meta is not None
        assert meta["file_path"] == str(module_file.resolve())
        assert meta["scope"] == "shared"

    def test_form_in_forms_package_init_registers(self, settings, tmp_path) -> None:
        """A Form in myapp/forms/__init__.py registers with its own file_path."""
        settings.BASE_DIR = tmp_path
        forms_pkg = tmp_path / "myapp" / "forms"
        forms_pkg.mkdir(parents=True)
        (tmp_path / "myapp" / "__init__.py").write_text("")
        module_file = forms_pkg / "__init__.py"
        module_file.write_text(
            "from next.forms import CharField, Form\n"
            "\n"
            "\n"
            "class PackagedInitForm(Form):\n"
            "    title = CharField()\n"
        )

        _exec_module_from_file("user_forms_pkg_init", module_file)

        backend = form_action_manager.default_backend
        meta = backend.get_meta("packaged_init_form")
        assert meta is not None
        assert meta["file_path"] == str(module_file.resolve())
        assert meta["scope"] == "shared"


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
    """Direct _auto_register_form_class calls cover the edge-case branches."""

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
                fields = ("name",)

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

    def test_concrete_subclass_of_abstract_base_registers(
        self, settings, tmp_path
    ) -> None:
        """A subclass inheriting Meta.abstract from its base still registers."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class AbstractTenantForm(Form):
                class Meta:
                    abstract = True

            class ConcreteInviteForm(AbstractTenantForm):
                email = django_forms.EmailField()

        backend = form_action_manager.default_backend
        assert backend.get_meta("abstract_tenant_form") is None
        assert backend.get_meta("concrete_invite_form") is not None

    def test_subclass_redeclaring_abstract_skips_registration(
        self, settings, tmp_path
    ) -> None:
        """A subclass with its own Meta.abstract = True stays unregistered."""
        settings.BASE_DIR = tmp_path
        app_dir = tmp_path / "myapp"
        app_dir.mkdir()
        (app_dir / "__init__.py").write_text("")
        fake_path = str(app_dir / "forms.py")
        Path(fake_path).write_text("")

        with patch("next.forms.base._find_definition_frame", return_value=fake_path):

            class RegisteredBaseForm(Form):
                title = django_forms.CharField()

            class ReabstractedForm(RegisteredBaseForm):
                class Meta:
                    abstract = True

        backend = form_action_manager.default_backend
        assert backend.get_meta("registered_base_form") is not None
        assert backend.get_meta("reabstracted_form") is None


class TestSelfRegisteredMarker:
    """_is_self_registered reflects the own-dict auto-registration marker."""

    def test_registered_class_is_marked(self) -> None:
        """A class that auto-registered carries the marker."""

        class MarkedForm(Form):
            name = django_forms.CharField()

        assert _is_self_registered(MarkedForm)

    def test_abstract_class_is_not_marked(self) -> None:
        """A Meta.abstract class skips registration and carries no marker."""

        class UnmarkedAbstractForm(Form):
            class Meta:
                abstract = True

        assert not _is_self_registered(UnmarkedAbstractForm)

    def test_marker_is_not_inherited_by_unregistered_subclass(self) -> None:
        """A subclass that opted out via Meta.abstract does not inherit the marker."""

        class MarkedBaseForm(Form):
            title = django_forms.CharField()

        class OptedOutForm(MarkedBaseForm):
            class Meta:
                abstract = True

        assert _is_self_registered(MarkedBaseForm)
        assert not _is_self_registered(OptedOutForm)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("ArticleEditForm", "article_edit_form"),
        ("Form", "form"),
        ("VoteForm", "vote_form"),
        ("CreateLinkForm", "create_link_form"),
        ("ContactUsForm", "contact_us_form"),
        ("XMLForm", "xml_form"),
        ("XMLParserForm", "xml_parser_form"),
        ("myform", "myform"),
    ],
)
def test_to_snake_case(name: str, expected: str) -> None:
    """_to_snake_case converts CamelCase class names to snake_case."""
    assert _to_snake_case(name) == expected


class TestIsFrameworkFileUsed:
    """_is_framework_file detects paths inside the framework root."""

    def test_framework_file_detected(self) -> None:
        """A path inside the framework root is recognised as a framework file."""
        framework_path = str(_FRAMEWORK_ROOT / "forms" / "base.py")
        assert _is_framework_file(framework_path) is True

    def test_non_framework_file_not_detected(self, tmp_path) -> None:
        """A path outside the framework root is not a framework file."""
        assert _is_framework_file(str(tmp_path / "myapp" / "forms.py")) is False


class TestComputeScope:
    """_compute_scope maps anchor file names to page scope, others to shared."""

    def test_default_anchor_names(self, tmp_path) -> None:
        """Without FORM_ANCHOR_FILES, page.py and component.py are anchors."""
        assert _compute_scope(str(tmp_path / "page.py")) == "page"
        assert _compute_scope(str(tmp_path / "component.py")) == "page"
        assert _compute_scope(str(tmp_path / "forms.py")) == "shared"

    def test_form_anchor_files_setting_overrides_defaults(self, tmp_path) -> None:
        """FORM_ANCHOR_FILES replaces which file names count as anchors."""
        with override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": ["screen.py"]}):
            next_framework_settings.reload()
            assert _compute_scope(str(tmp_path / "screen.py")) == "page"
            assert _compute_scope(str(tmp_path / "page.py")) == "shared"
        next_framework_settings.reload()

    def test_empty_form_anchor_files_disables_anchors(self, tmp_path) -> None:
        """An explicit empty FORM_ANCHOR_FILES means no file name is an anchor."""
        with override_settings(NEXT_FRAMEWORK={"FORM_ANCHOR_FILES": []}):
            next_framework_settings.reload()
            assert _compute_scope(str(tmp_path / "page.py")) == "shared"
            assert _compute_scope(str(tmp_path / "component.py")) == "shared"
        next_framework_settings.reload()
