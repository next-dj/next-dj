from typing import Literal

import pytest

from next.pages.checks.contexts import annotation_mismatch


def _returns_dict() -> dict:
    return {}


def _returns_mapping() -> dict[str, int]:
    return {}


def _returns_str() -> str:
    return ""


def _unannotated():
    return {}


def _returns_literal() -> Literal["x"]:
    return "x"


class TestReturnAnnotationHelpers:
    """The return-shape probe both the context and the metadata checks share."""

    @pytest.mark.parametrize(
        ("func", "expected"),
        [
            (_returns_dict, None),
            (_returns_mapping, None),
            (_returns_str, "str"),
            (_unannotated, None),
            (_returns_literal, "Literal"),
        ],
        ids=["dict", "generic_dict", "str", "unannotated", "non_type"],
    )
    def test_the_mismatch_is_read_off_the_resolved_hint(self, func, expected) -> None:
        assert annotation_mismatch(func) == expected
