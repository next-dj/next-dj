from pathlib import Path
from unittest import mock

import pytest

from next.router.checks import check_inline_scripts, scripts as router_checks
from tests.support.checks import write_file


@pytest.mark.usefixtures("router_on")
class TestInlineScripts:
    """`next.W177` names a template with an inline script without a nonce."""

    def test_scripts(self, tmp_path: Path) -> None:
        bare = write_file(tmp_path / "template.djx", "<script>go()</script>")
        fine = write_file(
            tmp_path / "a" / "template.djx",
            '<script nonce="{{ n }}">x</script><script src="/a.js"></script>'
            '<script type="application/json">{}</script>',
        )
        module = write_file(
            tmp_path / "b" / "template.djx", '<script type="module">y</script>'
        )
        with mock.patch.object(
            router_checks, "_templates", return_value=[bare, fine, module]
        ):
            messages = check_inline_scripts()
        assert [message.obj for message in messages] == [str(bare), str(module)]
        assert {message.id for message in messages} == {"next.W177"}
        assert "only when its nonce is the response nonce" in str(messages[0].hint)
        assert 'nonce="{{ csp_nonce }}"' in str(messages[0].hint)

    def test_the_templates_of_the_trees(self, tmp_path: Path) -> None:
        page = write_file(tmp_path / "a" / "template.djx", "<p>a</p>")
        layout = write_file(tmp_path / "layout.djx", "<main>{% template %}</main>")
        with (
            mock.patch.object(
                router_checks,
                "get_template_djx_paths_for_watch",
                return_value=[page, layout],
            ),
            mock.patch.object(router_checks, "layouts", return_value=[layout]),
        ):
            assert router_checks._templates() == sorted([page, layout])
