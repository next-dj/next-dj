from pathlib import Path

import pytest

from next.pages.checks.gating import check_hidden_guards
from tests.support.access import COMPONENTS
from tests.support.checks import run_check, write_files


_TRIP_PAGE = (
    "from django.http import Http404\n"
    "from next import page\n"
    "from next.pages import context\n\n"
    "page.public()\n\n"
    "def lookup():\n    raise Http404\n\n"
    "@context('item'{guard})\n"
    "def item():\n    return lookup()\n\n"
    "@context('title')\n"
    "def title():\n    return 'T'\n"
)
_LOADED = 'from next.components import component\n\ncomponent.defer("load")\n'


def _trip_tree(root: Path, body: str, *, guard: str = "", **files: str) -> Path:
    write_files(
        root,
        {
            "layout.djx": "<html><body>{% template %}</body></html>",
            "home/page.py": _TRIP_PAGE.format(guard=guard),
            "home/template.djx": body,
            "_components/card/component.py": _LOADED,
            "_components/card/component.djx": "<p>card</p>",
            "_components/plain/component.djx": "<p>plain</p>",
            "_components/outer/component.djx": "{% component 'card' %}",
            **files,
        },
    )
    return root / "home" / "page.py"


class TestReloadedGuards:
    """next.W169 flags an unguarded status check of a page that a client trip reloads."""

    @pytest.mark.parametrize(
        "body",
        [
            pytest.param("{% component 'card' %}", id="declared-mode"),
            pytest.param("{% component 'plain' defer='visible' %}", id="call-site"),
            pytest.param("{% component 'outer' %}", id="nested"),
        ],
    )
    def test_a_client_trip_page_is_flagged(self, tmp_path: Path, body: str) -> None:
        root = tmp_path / "pages"
        page_file = _trip_tree(root, body)

        messages = run_check(check_hidden_guards, root, COMPONENT_BACKENDS=COMPONENTS)

        assert [(m.id, Path(m.obj).resolve()) for m in messages] == [
            ("next.W169", page_file.resolve())
        ]
        assert "never runs it" in messages[0].msg
        assert "guard=True" in messages[0].hint

    @pytest.mark.parametrize(
        ("body", "guard"),
        [
            pytest.param("{% component 'plain' %}", "", id="no-trip"),
            pytest.param("{% component 'card' %}", ", guard=True", id="declared"),
            pytest.param("{% component 'missing' %}", "", id="unknown"),
        ],
    )
    def test_a_page_without_an_unguarded_trip_is_quiet(
        self, tmp_path: Path, body: str, guard: str
    ) -> None:
        root = tmp_path / "pages"
        _trip_tree(root, body, guard=guard)

        assert run_check(check_hidden_guards, root, COMPONENT_BACKENDS=COMPONENTS) == []

    def test_a_component_template_that_fails_is_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "pages"
        _trip_tree(
            root,
            "{% component 'broken' %}{% component 'bare' %}",
            **{
                "_components/broken/component.djx": "{% if %}",
                "_components/bare/component.py": "x = 1\n",
            },
        )

        assert run_check(check_hidden_guards, root, COMPONENT_BACKENDS=COMPONENTS) == []
