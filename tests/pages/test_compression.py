from collections.abc import Callable
from pathlib import Path

import pytest

from next.pages.compression import CompressDeclaration, compress_store, page_compress
from next.pages.loaders import load_python_module_memo


@pytest.mark.usefixtures("fresh_declarations")
class TestPageCompress:
    @pytest.mark.parametrize("enabled", [False, True])
    def test_a_bool_is_the_choice(
        self,
        tmp_path: Path,
        page_declaring: Callable[[Path, str], Path],
        *,
        enabled: bool,
    ) -> None:
        page_file = page_declaring(tmp_path, f"page.compress({enabled})")
        load_python_module_memo(page_file)
        assert page_compress(page_file) is enabled

    def test_a_value_that_is_no_bool_declares_nothing(
        self, tmp_path: Path, page_declaring: Callable[[Path, str], Path]
    ) -> None:
        page_file = page_declaring(tmp_path, "page.compress(0)")
        load_python_module_memo(page_file)
        assert page_compress(page_file) is None
        [(file_path, held)] = list(compress_store.loaded())
        assert file_path == page_file
        assert held.problem() == "enabled is 0, which is no bool"


class TestCompressDeclaration:
    def test_a_bool_is_the_choice(self) -> None:
        declaration = CompressDeclaration(False, {}, top_level=True)
        assert declaration.problem() is None
        assert declaration.choice() is False
