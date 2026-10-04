from next.site import SiteOriginError


class TestSiteOriginError:
    """The origin error names the setting and, when one stayed relative, the URL."""

    def test_names_the_url(self) -> None:
        error = SiteOriginError("/wallet/")
        assert str(error) == (
            "making '/wallet/' absolute needs a request or "
            "NEXT_FRAMEWORK['SITE']['URL'] for its origin"
        )
        assert error.url == "/wallet/"
        assert isinstance(error, ValueError)

    def test_without_a_url_names_any_absolute_url(self) -> None:
        error = SiteOriginError()
        assert str(error) == (
            "an absolute URL needs a request or NEXT_FRAMEWORK['SITE']['URL'] "
            "for its origin"
        )
        assert error.url is None
