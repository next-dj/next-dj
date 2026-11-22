from pathlib import Path

from django.test import override_settings

import next as next_package
from next.components.paths import anchored


class TestAnchored:
    """A template is named relative to what holds it, not by its host path."""

    def test_a_relative_name_stands(self) -> None:
        assert anchored("page.html") == "page.html"

    def test_an_app_file_is_named_by_its_app(self) -> None:
        inside = Path(next_package.__file__).resolve().parent / "x" / "t.djx"
        assert anchored(str(inside)) == "next:x/t.djx"

    def test_a_root_file_is_named_by_its_root(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        entry = {"BACKEND": "next.urls.FileRouterBackend", "DIRS": [str(pages)]}
        with override_settings(
            NEXT_FRAMEWORK={
                "PAGE_BACKENDS": [entry],
                "COMPONENT_BACKENDS": [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "COMPONENTS_DIR": "_components",
                    }
                ],
            }
        ):
            assert anchored(str(pages / "a" / "page.py")) == "0:a/page.py"

    def test_a_base_dir_file_is_named_by_its_path(self, tmp_path: Path) -> None:
        with override_settings(BASE_DIR=tmp_path):
            assert anchored(str(tmp_path / "t" / "x.djx")) == "t/x.djx"

    def test_a_file_outside_everything_keeps_its_path(self, tmp_path: Path) -> None:
        with override_settings(BASE_DIR=None):
            assert anchored(str(tmp_path / "far.djx")) == str(tmp_path / "far.djx")
