from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

from next.pages.loaders import load_python_module_memo
from next.pages.router import router_store
from next.router.checks import (
    check_no_store_pages,
    check_router_declarations,
    pages as router_checks,
)
from next.testing import override_next_settings
from tests.support.checks import write_file


@pytest.fixture()
def router_on() -> Iterator[None]:
    with override_next_settings(ROUTER={"ENABLED": True}):
        yield


@pytest.mark.usefixtures("router_on")
class TestNoStorePages:
    """`next.W178` names a page whose cache forbids every store."""

    def test_no_store(self, tmp_path: Path) -> None:
        stored = write_file(tmp_path / "a" / "page.py", "cache = False\n")
        cached = write_file(tmp_path / "b" / "page.py", "cache = 60\n")
        plain = write_file(tmp_path / "c" / "page.py", "x = 1\n")
        with mock.patch.object(
            router_checks, "_page_modules", return_value=iter([stored, cached, plain])
        ):
            (message,) = check_no_store_pages()
        assert message.id == "next.W178"
        assert str(stored) in message.msg

    def test_the_pages_of_the_trees(self, tmp_path: Path) -> None:
        write_file(tmp_path / "a" / "page.py", "x = 1\n")
        with mock.patch.object(
            router_checks, "get_pages_directories_for_watch", return_value=[tmp_path]
        ):
            assert list(router_checks._page_modules()) == [tmp_path / "a" / "page.py"]


class TestDeclarations:
    """`next.E238` names a `page.router()` call that applies nothing."""

    @pytest.fixture(autouse=True)
    def _fresh(self) -> Iterator[None]:
        router_store.reset()
        yield
        router_store.reset()

    def test_a_bad_call_and_a_misplaced_one(self, tmp_path: Path) -> None:
        load_python_module_memo(
            write_file(
                tmp_path / "a" / "page.py",
                "from next.pages import page\npage.router(prefetch='no')\n",
            )
        )
        load_python_module_memo(
            write_file(
                tmp_path / "helpers.py", "from next.pages import page\npage.router()\n"
            )
        )
        messages = check_router_declarations()
        assert [message.id for message in messages] == ["next.E238", "next.E238"]
        assert "prefetch='no' is no bool" in messages[0].msg
        assert "outside a page.py" in messages[1].msg
