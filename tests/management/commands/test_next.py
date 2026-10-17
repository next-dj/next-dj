from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from tests.support import installed_apps, write_app, write_urlconf


class TestNextCommand:
    """`next` dispatches to the subcommand named on its command line."""

    def test_no_subcommand_is_refused(self) -> None:
        with pytest.raises(CommandError, match="subcommand"):
            call_command("next")

    def test_an_unknown_subcommand_is_refused(self) -> None:
        with pytest.raises(CommandError, match="invalid choice: 'nope'"):
            call_command("next", "nope")

    def test_a_subcommand_writes_through_the_command_streams(self, tmp_path) -> None:
        write_app(tmp_path, "blog", {"pages": ("about",)})
        urlconf = write_urlconf(tmp_path, "cmd_urls", 'path("", include("next.urls"))')
        out, err = StringIO(), StringIO()

        with installed_apps(tmp_path, "blog", urlconf=urlconf, BASE_DIR=tmp_path):
            call_command("next", "routes", stdout=out, stderr=err)

        assert out.getvalue().splitlines() == [
            "URL      Name             App   File                      Source  Access",
            "/about/  next:page_about  blog  blog/pages/about/page.py  root    -",
        ]
        assert err.getvalue() == ""

    def test_the_help_lists_every_subcommand(self, capsys) -> None:
        with pytest.raises(SystemExit):
            call_command("next", "--help")

        assert "routes" in capsys.readouterr().out
