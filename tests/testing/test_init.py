import next.testing
import next.testing.seo


class TestTestingPublicSurface:
    """The curated `next.testing` surface carries the SEO parse results."""

    def test_the_parse_result_types_are_exported(self) -> None:
        exported = set(next.testing.__all__)
        assert set(next.testing.seo.__all__) <= exported
        assert all(hasattr(next.testing, name) for name in next.testing.__all__)
