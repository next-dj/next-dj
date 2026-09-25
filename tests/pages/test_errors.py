from pathlib import Path

from next.pages.errors import (
    PageMetadataConflictError,
    PageMetadataShapeError,
    PageMetadataTemplateError,
    PageMetadataURLError,
)


class TestMetadataErrors:
    """Each metadata error names what went wrong and keeps it as attributes."""

    def test_shape_error(self) -> None:
        error = PageMetadataShapeError("pages/wallet/page.py", "returned a non-mapping")
        assert str(error) == "pages/wallet/page.py returned a non-mapping"
        assert error.source == "pages/wallet/page.py"
        assert error.detail == "returned a non-mapping"
        assert isinstance(error, TypeError)

    def test_conflict_error(self) -> None:
        path = Path("pages/wallet/page.py")
        error = PageMetadataConflictError(path)
        assert str(error) == (
            "pages/wallet/page.py declares both a metadata dict and an "
            "@page.metadata callable"
        )
        assert error.file_path == path
        assert isinstance(error, ValueError)

    def test_url_error(self) -> None:
        error = PageMetadataURLError("/wallet/")
        assert str(error) == (
            "cannot make '/wallet/' absolute without a request or a metadata base"
        )
        assert error.url == "/wallet/"
        assert isinstance(error, ValueError)

    def test_template_error(self) -> None:
        error = PageMetadataTemplateError("{nope}", "names the placeholder 'nope'")
        assert str(error) == (
            "metadata title template '{nope}' names the placeholder 'nope'"
        )
        assert error.template == "{nope}"
        assert error.detail == "names the placeholder 'nope'"
        assert isinstance(error, ValueError)
