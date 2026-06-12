import pytest
from django.test import override_settings

from next.conf import next_framework_settings
from next.forms.backends import (
    ActionRegistration,
    RegistryFormActionBackend,
    file_to_dotted_module,
)
from next.forms.base import (
    _FRAMEWORK_ROOT,
    _compute_scope,
    _is_framework_file,
    _to_snake_case,
)


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
