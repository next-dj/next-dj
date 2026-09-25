from typing import Annotated

import pytest

from next.deps import resolver
from next.pages.metadata import Metadata
from next.pages.metadata.chain import PARENT_KEY
from next.pages.metadata.providers import ParentMetadataProvider
from next.pages.metadata.schema import EMPTY_METADATA
from next.testing import make_resolution_context
from tests.support import inspect_parameter


PARENT = Metadata(title="Parent")


@pytest.fixture()
def provider() -> ParentMetadataProvider:
    return ParentMetadataProvider()


class TestClaim:
    """The provider claims a parameter on its `Metadata` annotation alone."""

    @pytest.mark.parametrize(
        "annotation",
        [Metadata, Annotated[Metadata, "parent"]],
        ids=["plain", "annotated"],
    )
    def test_claims_a_metadata_annotation_statically(
        self, provider: ParentMetadataProvider, annotation: object
    ) -> None:
        param = inspect_parameter("parent", annotation)
        assert provider.static_can_handle(param) is True
        assert provider.can_handle(param, make_resolution_context()) is True

    @pytest.mark.parametrize(
        "annotation",
        [str, Metadata | None, inspect_parameter("x").annotation],
        ids=["str", "optional", "bare"],
    )
    def test_refuses_any_other_annotation_for_good(
        self, provider: ParentMetadataProvider, annotation: object
    ) -> None:
        param = inspect_parameter("parent", annotation)
        assert provider.static_can_handle(param) is False
        assert provider.can_handle(param, make_resolution_context()) is False

    def test_sits_between_the_context_marker_and_the_name_lookup(self) -> None:
        assert ParentMetadataProvider.priority == 25


class TestResolve:
    """The parent fold comes off the context, or nothing outside a metadata resolve."""

    def test_reads_the_parent_the_resolve_published(
        self, provider: ParentMetadataProvider
    ) -> None:
        context = make_resolution_context(context_data={PARENT_KEY: PARENT})
        param = inspect_parameter("parent", Metadata)
        assert provider.resolve(param, context) is PARENT

    def test_answers_the_empty_metadata_outside_a_resolve(
        self, provider: ParentMetadataProvider
    ) -> None:
        param = inspect_parameter("parent", Metadata)
        assert provider.resolve(param, make_resolution_context()) is EMPTY_METADATA

    def test_the_compiled_filler_reads_the_same_key(
        self, provider: ParentMetadataProvider
    ) -> None:
        filler = provider.compile_resolve(inspect_parameter("parent", Metadata))
        context = make_resolution_context(context_data={PARENT_KEY: PARENT})
        assert filler(context) is PARENT
        assert filler(make_resolution_context()) is EMPTY_METADATA


class TestThroughTheResolver:
    """The registered provider fills `parent: Metadata` on a real resolve."""

    def test_a_metadata_parameter_is_filled_from_the_context(self) -> None:
        def meta(parent: Metadata, slug: str) -> dict[str, str]:
            return {"title": f"{parent.title}/{slug}"}

        resolved = resolver.resolve_dependencies(
            meta, _context_data={PARENT_KEY: PARENT}, slug="s"
        )
        assert meta(**resolved) == {"title": "Parent/s"}

    def test_a_metadata_parameter_is_empty_outside_a_metadata_resolve(self) -> None:
        def meta(parent: Metadata) -> Metadata:
            return parent

        resolved = resolver.resolve_dependencies(meta)
        assert resolved["parent"] is EMPTY_METADATA
