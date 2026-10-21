from pathlib import Path
from typing import Literal

import pytest

from next.pages.checks.contexts import (
    annotation_mismatch,
    check_context_registration_files,
)
from next.pages.loaders import load_python_module_memo
from tests.support import routed, write_page
from tests.support.access import page_source


_ORG = 'from next.pages import context\n\n@context("org")\ndef org():\n    return 1\n'


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


class TestStrayContexts:
    """next.E074 flags a `@context` outside a `page.py`, which no render collects."""

    def test_a_helper_context_is_flagged(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        write_page(root, "a", page_source())
        declaring = root / "a" / "helpers.py"
        declaring.write_text(_ORG)
        load_python_module_memo(declaring)

        with routed(root):
            messages = check_context_registration_files()

        assert [(m.id, m.obj) for m in messages] == [("next.E074", str(declaring))]
        assert "is not a page.py" in messages[0].msg
