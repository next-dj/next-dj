import pytest

from next.boundaries.graph import Graph
from next.boundaries.kernel import Kernel, is_kernel, kernels
from tests.support.boundaries import scanned


_KERNEL_PROJECT = {
    "core": {
        "utils.py": "a = 1\n\n   \nb = 2\n",
        "services.py": "def ping():\n    pass\n",
        "tests/test_utils.py": "x = 1\ny = 2\n",
        "migrations/__init__.py": "",
        "migrations/0001_initial.py": "x = 1\n",
    },
    "shop": {"views.py": "from core.services import ping\nimport core.services\n"},
    "crm": {"views.py": "from core.services import ping\n"},
    "billing": {},
}


class TestKernel:
    """The app named `core` is the shared kernel, its size and its users counted."""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("core", True),
            ("proj.core", True),
            ("proj.core.extra", False),
            ("score", False),
            ("core_x", False),
        ],
    )
    def test_the_name(self, name, expected) -> None:
        assert is_kernel(name) is expected

    def test_its_size_and_users(self, tmp_path) -> None:
        scan = scanned(tmp_path, _KERNEL_PROJECT)

        assert kernels(scan.project, scan.graph) == (Kernel("core", 4, 7, 2),)

    def test_a_project_without_one(self, tmp_path) -> None:
        scan = scanned(tmp_path, {"shop": {}, "crm": {}})

        assert kernels(scan.project, scan.graph) == ()

    def test_no_project_has_none(self) -> None:
        assert kernels(None, Graph((), ())) == ()
