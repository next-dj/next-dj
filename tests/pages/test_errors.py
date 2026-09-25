from pathlib import Path

from next.pages.errors import (
    PageMetadataConflictError,
    PageMetadataRequestError,
    PageMetadataShapeError,
    PageMetadataTemplateError,
    PageMetadataURLError,
)


class TestMetadataErrors:
    """Each metadata error names what went wrong and keeps it as attributes."""

    def test_a_shape_error_names_the_source_and_the_detail(self) -> None:
        error = PageMetadataShapeError("pages/wallet/page.py", "returned a non-mapping")
        assert str(error) == "pages/wallet/page.py returned a non-mapping"
        assert error.source == "pages/wallet/page.py"
        assert error.detail == "returned a non-mapping"
        assert isinstance(error, TypeError)

    def test_a_conflict_error_names_the_file(self) -> None:
        path = Path("pages/wallet/page.py")
        error = PageMetadataConflictError(path)
        assert str(error) == (
            "pages/wallet/page.py declares both a metadata dict and an "
            "@page.metadata callable"
        )
        assert error.file_path == path
        assert isinstance(error, ValueError)

    def test_a_url_error_names_the_url(self) -> None:
        error = PageMetadataURLError("/wallet/")
        assert str(error) == (
            "cannot make '/wallet/' absolute without a request or a metadata base"
        )
        assert error.url == "/wallet/"
        assert isinstance(error, ValueError)

    def test_a_request_error_names_the_key(self) -> None:
        error = PageMetadataRequestError("canonical")
        assert (
            str(error) == "`canonical=True` names the page itself and needs a request"
        )
        assert error.key == "canonical"
        assert isinstance(error, ValueError)
        assert not isinstance(error, PageMetadataURLError)

    def test_a_template_error_names_the_template_and_the_detail(self) -> None:
        error = PageMetadataTemplateError("{nope}", "names the placeholder 'nope'")
        assert str(error) == (
            "metadata title template '{nope}' names the placeholder 'nope'"
        )
        assert error.template == "{nope}"
        assert error.detail == "names the placeholder 'nope'"
        assert isinstance(error, ValueError)
