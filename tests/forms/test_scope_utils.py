import pytest

from next.forms.backends import _file_to_dotted_module
from next.forms.base import _FRAMEWORK_ROOT, _is_framework_file, _to_snake_case


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
    """_file_to_dotted_module returns dotted module path for files inside packages."""

    def test_standalone_file_returns_stem(self, tmp_path) -> None:
        """File not in a package returns just the file stem."""
        f = tmp_path / "mymodule.py"
        f.write_text("")
        assert _file_to_dotted_module(str(f)) == "mymodule"

    def test_file_in_package_returns_dotted_name(self, tmp_path) -> None:
        """File inside a package returns package.module when a parent __init__.py exists."""
        (tmp_path / "__init__.py").write_text("")
        pkg = tmp_path / "myapp"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        f = pkg / "forms.py"
        f.write_text("")
        result = _file_to_dotted_module(str(f))
        assert result == "myapp.forms"

    def test_nested_package(self, tmp_path) -> None:
        """Deeply nested package returns full dotted path when all ancestor inits exist."""
        deep = tmp_path / "a" / "b"
        deep.mkdir(parents=True)
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "a" / "__init__.py").write_text("")
        (deep / "__init__.py").write_text("")
        f = deep / "forms.py"
        f.write_text("")
        result = _file_to_dotted_module(str(f))
        assert result == "a.b.forms"


class TestIsFrameworkFileUsed:
    """_is_framework_file detects paths inside the framework root."""

    def test_framework_file_detected(self) -> None:
        """A path inside the framework root is recognised as a framework file."""
        framework_path = str(_FRAMEWORK_ROOT / "forms" / "base.py")
        assert _is_framework_file(framework_path) is True

    def test_non_framework_file_not_detected(self, tmp_path) -> None:
        """A path outside the framework root is not a framework file."""
        assert _is_framework_file(str(tmp_path / "myapp" / "forms.py")) is False
