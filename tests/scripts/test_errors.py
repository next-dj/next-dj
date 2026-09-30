from pathlib import Path

from next.scripts import ScriptsSourceImportError


class TestErrors:
    """The errors name what failed."""

    def test_a_source_import_error_names_the_file(self) -> None:
        error = ScriptsSourceImportError(Path("/pages/scripts.py"))
        assert error.path == Path("/pages/scripts.py")
        assert "/pages/scripts.py failed to import" in str(error)
