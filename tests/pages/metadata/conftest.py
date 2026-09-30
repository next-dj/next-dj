import pytest

from next.pages.metadata.registry import PageMetadataRegistry


@pytest.fixture()
def registry() -> PageMetadataRegistry:
    return PageMetadataRegistry()
