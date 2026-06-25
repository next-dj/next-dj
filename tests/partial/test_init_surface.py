import pytest

import next.partial
import next.partial.headers
import next.partial.origin
import next.partial.patches
import next.partial.registry
from next.partial import (
    Asset,
    Envelope,
    ForeignPageNotAuthorizedError,
    FormMeta,
    Patch,
    Patches,
    PatchResponse,
    UnknownZoneError,
)


_CURATED = frozenset(
    {
        "Asset",
        "Envelope",
        "ForeignPageNotAuthorizedError",
        "FormMeta",
        "Patch",
        "PartialProtocolBackend",
        "PatchEventStream",
        "PatchResponse",
        "Patches",
        "UnknownZoneError",
        "ZoneRenderResult",
        "is_partial_request",
        "partial_intent",
        "register_patch_op",
        "render_zone",
        "resolve_partial_origin",
        "shape_partial",
        "signals",
        "zone_requested",
    }
)

_DEMOTED = {
    "OriginSource": next.partial.origin,
    "ZoneInfo": next.partial.registry,
    "zones_of": next.partial.registry,
    "REQUEST_ID": next.partial.headers,
    "BuiltinPatchOpError": next.partial.patches,
    "CrossSiteHrefError": next.partial.patches,
    "DynamicForeignPageError": next.partial.patches,
    "ReservedEventNameError": next.partial.patches,
    "ReservedPatchKeyError": next.partial.patches,
    "UnknownContextNameError": next.partial.patches,
    "UnknownDedupeError": next.partial.patches,
    "UnknownPatchOpError": next.partial.patches,
}


class TestCuratedSurface:
    """`next.partial.__all__` is the frozen curated surface."""

    def test_all_matches_curated_set(self) -> None:
        assert frozenset(next.partial.__all__) == _CURATED

    def test_all_has_no_duplicates(self) -> None:
        assert len(next.partial.__all__) == len(set(next.partial.__all__))

    @pytest.mark.parametrize("name", sorted(_CURATED))
    def test_every_curated_name_resolves(self, name: str) -> None:
        assert getattr(next.partial, name) is not None


class TestDemotedNames:
    """Demoted names leave the facade but stay reachable by submodule."""

    @pytest.mark.parametrize("name", sorted(_DEMOTED))
    def test_name_not_in_all(self, name: str) -> None:
        assert name not in next.partial.__all__

    @pytest.mark.parametrize("name", sorted(_DEMOTED))
    def test_name_not_a_facade_reexport(self, name: str) -> None:
        assert not hasattr(next.partial, name)

    @pytest.mark.parametrize("name", sorted(_DEMOTED))
    def test_name_reachable_on_submodule(self, name: str) -> None:
        assert getattr(_DEMOTED[name], name) is not None


class TestFrozenImportPaths:
    """Value objects and public errors import from the aggregator facade.

    These import paths are frozen against the 0.9 split of the god-module,
    so the test pins them on `next.partial` rather than `next.partial.patches`.
    """

    def test_value_objects_import_from_facade(self) -> None:
        assert Patch is next.partial.patches.Patch
        assert Envelope is next.partial.patches.Envelope
        assert Asset is next.partial.patches.Asset
        assert FormMeta is next.partial.patches.FormMeta
        assert Patches is next.partial.patches.Patches
        assert PatchResponse is next.partial.patches.PatchResponse

    def test_public_errors_import_from_facade(self) -> None:
        assert (
            ForeignPageNotAuthorizedError
            is next.partial.patches.ForeignPageNotAuthorizedError
        )
        assert UnknownZoneError is not None
