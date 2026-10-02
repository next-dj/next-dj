import next.pages
import next.pages.metadata


class TestPagesReexports:
    """`next.pages` hands out the very metadata objects its subpackage defines."""

    def test_the_pages_package_reexports_the_reset_marker_and_the_resolve(self) -> None:
        assert {"RESET", "Replace", "ResolvedMetadata", "ld"} <= set(next.pages.__all__)
        assert next.pages.RESET is next.pages.metadata.RESET
        assert next.pages.ld is next.pages.metadata.ld
