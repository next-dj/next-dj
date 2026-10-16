from argparse import ArgumentParser
from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import BaseCommand

from next.management import subcommands
from next.management.subcommands import BaseSubcommand, load_subcommands
from tests.support import importable_dir


_SUBCOMMAND = (
    "from next.management.subcommands import BaseSubcommand\n\n\n"
    "class Subcommand(BaseSubcommand):\n"
    "    help = {help!r}\n\n"
    "    def add_arguments(self, parser):\n"
    "        parser.add_argument('--loud', action='store_true')\n\n"
    "    def handle(self, command, **options):\n"
    "        command.stdout.write(f'{help} loud={{options[\"loud\"]}}')\n"
)


@pytest.fixture()
def extra_subcommands(tmp_path, monkeypatch):
    for name in ("zeta", "alpha", "_hidden"):
        (tmp_path / f"{name}.py").write_text(_SUBCOMMAND.format(help=name))
    monkeypatch.setattr(subcommands, "__path__", [*subcommands.__path__, str(tmp_path)])
    with importable_dir(tmp_path):
        yield


class TestLoadSubcommands:
    """Every public module of the package is one subcommand, found by name."""

    def test_the_shipped_subcommands(self) -> None:
        assert list(load_subcommands()) == ["routes"]

    @pytest.mark.usefixtures("extra_subcommands")
    def test_a_new_module_is_a_new_subcommand_in_name_order(self) -> None:
        loaded = load_subcommands()

        assert list(loaded) == ["alpha", "routes", "zeta"]
        assert all(isinstance(each, BaseSubcommand) for each in loaded.values())

    @pytest.mark.usefixtures("extra_subcommands")
    def test_the_dispatcher_hands_a_new_subcommand_its_options(self) -> None:
        out = StringIO()

        call_command("next", "alpha", "--loud", stdout=out)

        assert out.getvalue() == "alpha loud=True\n"


class TestBaseSubcommand:
    """The base declares no options and leaves the work to the subclass."""

    def test_it_declares_no_options(self) -> None:
        parser = ArgumentParser()

        BaseSubcommand().add_arguments(parser)

        assert parser.parse_args([]).__dict__ == {}

    def test_a_subcommand_without_handle_refuses_to_run(self) -> None:
        with pytest.raises(NotImplementedError):
            BaseSubcommand().handle(BaseCommand())
