from collections.abc import Iterator
from pathlib import Path

import pytest
from django.core.checks import CheckMessage

from next.pages.checks.gating import check_gated_reads, check_hidden_guards
from next.pages.loaders import forget_layout_texts
from tests.support import patch_checks_route_manager, write_page


_GATED = (
    "from next.pages import context\n\n"
    "@context('product')\n"
    "def product():\n    return 1\n\n"
    "@context('summary')\n"
    "def summary():\n    return 2\n\n"
    "template = 'ok'\n"
)

_TITLED = (
    "from next import page\n"
    "from next.pages import context\n\n"
    "@context('headline')\n"
    "def headline():\n    return 'h'\n\n"
    "@page.metadata\n"
    "def meta_title(headline):\n    return {'title': headline}\n\n"
    "template = 'ok'\n"
)

_HELPED = (
    "from django.http import Http404\n"
    "from next.pages import context\n\n"
    "def lookup():\n    raise Http404\n\n"
    "@context('item')\n"
    "def item():\n    return lookup()\n\n"
    "template = 'ok'\n"
)

_DEEP = (
    "from django.http import Http404\n"
    "from next.pages import context\n\n"
    "def four():\n    raise Http404\n\n"
    "def three():\n    return four()\n\n"
    "def two():\n    return three()\n\n"
    "def one():\n    return two()\n\n"
    "@context('item')\n"
    "def item():\n    return one()\n\n"
    "template = 'ok'\n"
)

_WRAPPING = (
    "from next.pages import context\n\n"
    "@context('product')\n"
    "def product():\n    return 1\n"
)


def _tree(root: Path, source: str, layout: str = "<html>{% template %}</html>") -> Path:
    (root / "layout.djx").write_text(layout)
    (root / "shop").mkdir()
    (root / "shop" / "loading.djx").write_text("<p>loading</p>")
    forget_layout_texts()
    return write_page(root, "shop", source)


pytestmark = pytest.mark.usefixtures("framework_providers")


def _flags(messages: list[CheckMessage]) -> list[tuple[str, str | None]]:
    return [(message.id, message.obj) for message in messages]


@pytest.fixture(autouse=True)
def _router(tmp_path: Path) -> Iterator[None]:
    with patch_checks_route_manager(pages_directory=tmp_path):
        yield


class TestGatedReads:
    """next.W152 flags a read outside a page boundary of a key the boundary gates."""

    def test_a_layout_above_the_boundary_reading_a_gated_key_is_flagged(
        self, tmp_path: Path
    ) -> None:
        page_file = _tree(
            tmp_path, _GATED, "<html><title>{{ product }}</title>{% template %}</html>"
        )

        messages = check_gated_reads()

        assert _flags(messages) == [("next.W152", str(page_file))]
        assert "'product'" in messages[0].msg

    def test_a_layout_reading_no_gated_key_is_quiet(self, tmp_path: Path) -> None:
        _tree(tmp_path, _GATED, "<html>{{ other }}{% template %}</html>")

        assert check_gated_reads() == []

    def test_a_page_without_a_loading_file_is_quiet(self, tmp_path: Path) -> None:
        (tmp_path / "layout.djx").write_text("<html>{{ product }}{% template %}</html>")
        write_page(tmp_path, "shop", _GATED)

        assert check_gated_reads() == []

    def test_a_loading_file_reading_a_gated_key_is_flagged(
        self, tmp_path: Path
    ) -> None:
        page_file = _tree(tmp_path, _GATED)
        (tmp_path / "shop" / "loading.djx").write_text("<p>{{ summary }}</p>")
        forget_layout_texts()

        messages = check_gated_reads()

        assert _flags(messages) == [("next.W152", str(page_file))]
        assert "'summary'" in messages[0].msg
        assert "loading UI" in messages[0].msg

    def test_a_page_py_without_a_body_is_not_checked(self, tmp_path: Path) -> None:
        _tree(tmp_path, _WRAPPING, "<html>{{ product }}{% template %}</html>")
        write_page(tmp_path, "shop/item", body="<p>item</p>")

        assert check_gated_reads() == []

    def test_metadata_reading_a_gated_key_is_flagged(self, tmp_path: Path) -> None:
        page_file = _tree(tmp_path, _TITLED)

        messages = check_gated_reads()

        assert _flags(messages) == [("next.W152", str(page_file))]
        assert "'headline'" in messages[0].msg


class TestHiddenGuards:
    """next.W169 flags a deferred callable the scan sees answer a status."""

    def test_a_guard_past_the_scan_depth_is_flagged(self, tmp_path: Path) -> None:
        page_file = _tree(tmp_path, _DEEP)

        messages = check_hidden_guards()

        assert _flags(messages) == [("next.W169", str(page_file))]
        assert "'item'" in messages[0].msg
        assert "four" in messages[0].msg
        assert "guard=True" in messages[0].hint

    def test_a_guard_the_scan_follows_is_flagged(self, tmp_path: Path) -> None:
        page_file = _tree(tmp_path, _HELPED)

        messages = check_hidden_guards()

        assert _flags(messages) == [("next.W169", str(page_file))]
        assert "answers 404, 403 or 400" in messages[0].msg

    def test_a_declared_guard_runs_in_the_shell_and_is_quiet(
        self, tmp_path: Path
    ) -> None:
        _tree(
            tmp_path,
            _HELPED.replace("@context('item')", "@context('item', guard=True)"),
        )

        assert check_hidden_guards() == []

    def test_a_page_with_plain_callables_is_quiet(self, tmp_path: Path) -> None:
        _tree(tmp_path, _GATED)

        assert check_hidden_guards() == []
