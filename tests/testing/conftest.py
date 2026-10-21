from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support import isolated_form_registries, routed, write_twin_pages


@pytest.fixture()
def twins(tmp_path: Path) -> Iterator[Path]:
    """Route two pages that each declare an action named `twin`."""
    root = write_twin_pages(tmp_path / "pages")
    with isolated_form_registries(), routed(root):
        yield root
