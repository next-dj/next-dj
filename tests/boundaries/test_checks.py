from collections import defaultdict
from io import StringIO

import pytest
from django.apps import apps as django_apps
from django.conf import ENVIRONMENT_VARIABLE, settings as django_settings
from django.core.checks import Error, Warning as DjangoWarning, run_checks
from django.core.checks.model_checks import check_lazy_references
from django.core.checks.registry import registry
from django.core.management import CommandError, call_command, get_commands
from django.core.management.base import BaseCommand, SystemCheckError
from django.test import override_settings

from next.boundaries import imports as imports_module
from next.boundaries.checks import boundary_checks, check_boundaries, check_declarations
from next.boundaries.manager import boundaries_manager
from next.boundaries.rules import (
    CLOSURE,
    CYCLE,
    DECLARATIONS,
    DOCTRINE,
    RULES,
    SCAN,
    STRICT_OFF,
    UNRECORDED,
)
from next.checks import BOUNDARIES, NEXT
from next.conf.checks import check_next_framework_boundaries_mode
from tests.support import importable_dir
from tests.support.boundaries import Probes, app_config, boundary_project, held_checks


_PROJECT = {
    "shop": {"views.py": "from crm.utils import helper\n"},
    "crm": {"utils.py": "helper = 1\n"},
}

_W = "next.W136"

_STRICT = {"BOUNDARIES": "strict"}


def _boundary_ids(messages) -> list[str]:
    return [message.id for message in messages if message.id in RULES]


def _own_ids() -> list[str]:
    return sorted(message.id for message in boundary_checks.run_checks())


def _output(*args: str) -> str:
    out, err = StringIO(), StringIO()
    call_command("check", *args, stdout=out, stderr=err)
    return out.getvalue() + err.getvalue()


def _lent() -> set[object]:
    return set(boundary_checks.get_checks()) & registry.registered_checks


class TestCheckBoundaries:
    """Each finding is one check message, its hint pointing to the doctrine."""

    def test_a_warning_and_the_strict_error(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework={"BOUNDARIES": "strict"}):
            warning, error = check_boundaries()

        assert type(warning) is DjangoWarning
        assert warning.id == _W
        assert warning.obj == "shop/views.py:1"
        assert warning.msg == (
            "shop imports crm.utils, which crm does not publish. Only its models, "
            "services and signals modules are public."
        )
        assert warning.hint == (
            "Import from the models, services or signals module of the other app, or "
            f"move what you need there. See {DOCTRINE}#public-modules."
        )
        assert type(error) is Error
        assert (error.id, error.obj) == (UNRECORDED, "boundaries.toml")

    def test_a_finding_without_a_file_names_no_object(self, tmp_path) -> None:
        broken = {"shop": {"views.py": "def broken(:\n"}}

        with boundary_project(tmp_path, broken):
            (message,) = check_boundaries()

        assert (type(message), message.id, message.obj) == (Error, SCAN, None)

    def test_without_grimp_the_ast_reader_checks(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)

        with boundary_project(tmp_path, _PROJECT):
            assert _own_ids() == [_W]

    def test_a_clean_project_has_no_message(self, tmp_path) -> None:
        with boundary_project(tmp_path, {"shop": {"views.py": "x = 1\n"}}):
            assert check_boundaries() == []


class TestOff:
    """Without `BOUNDARIES` the checks say nothing, not even about what is broken."""

    @pytest.mark.parametrize(
        "overrides", [{}, {"BASE_DIR": None}], ids=["base_dir", "no_base_dir"]
    )
    def test_silent(self, tmp_path, overrides) -> None:
        framework = {"BOUNDARIES": None}

        with boundary_project(
            tmp_path, _BROKEN_DECLARATION, framework=framework, **overrides
        ):
            assert (check_boundaries(), check_declarations()) == ([], [])
            with held_checks():
                output = _output("--tag", BOUNDARIES)

        assert output.strip() == "System check identified no issues (0 silenced)."


class TestOptIn:
    """Only `check --tag boundaries` runs them, lent to the registry for that run."""

    def test_a_plain_check_runs_none(self) -> None:
        probes = Probes()
        regular = probes.check("next", NEXT)

        with held_checks([regular], area=[probes.check("boundaries", BOUNDARIES)]):
            _output()
            lent = _lent()

        assert probes.calls == ["next"]
        assert lent == set()

    def test_the_tag_runs_them_and_lends_nothing_after(self) -> None:
        probes = Probes()
        regular = probes.check("next", NEXT)

        with held_checks([regular], area=[probes.check("boundaries", BOUNDARIES)]):
            _output("--tag", BOUNDARIES)
            _output()
            lent = _lent()

        assert probes.calls == ["boundaries", "next"]
        assert lent == set()

    def test_the_tag_with_another_tag(self) -> None:
        probes = Probes()
        regular = probes.check("next", NEXT)

        with held_checks([regular], area=[probes.check("boundaries", BOUNDARIES)]):
            _output("--tag", NEXT, "--tag", BOUNDARIES)

        assert sorted(probes.calls) == ["boundaries", "next"]

    def test_the_tag_fails_on_warnings_when_asked(self) -> None:
        probes = Probes()
        warning = DjangoWarning("A finding.", id=_W)
        area = [probes.check("boundaries", BOUNDARIES, messages=[warning])]

        with (
            held_checks(area=area),
            pytest.raises(SystemCheckError, match=r"next\.W136"),
        ):
            _output("--tag", BOUNDARIES, "--fail-level", "WARNING")

        assert _lent() == set()

    def test_the_deploy_check_runs_none(self) -> None:
        probes = Probes()
        regular = probes.check("next", NEXT)
        deployment = probes.check("deploy", "security")

        with held_checks(
            [regular],
            deployment=[deployment],
            area=[probes.check("boundaries", BOUNDARIES)],
        ):
            _output("--deploy")

        assert sorted(probes.calls) == ["deploy", "next"]

    def test_list_tags_shows_the_tag(self) -> None:
        probes = Probes()
        regular = probes.check("next", NEXT)

        with held_checks([regular], area=[probes.check("boundaries", BOUNDARIES)]):
            tags = _output("--list-tags").split()
            lent = _lent()

        assert tags == [BOUNDARIES, NEXT]
        assert probes.calls == []
        assert lent == set()

    def test_the_check_of_other_commands_runs_none(self) -> None:
        probes = Probes()
        regular = probes.check("next", NEXT)

        with held_checks([regular], area=[probes.check("boundaries", BOUNDARIES)]):
            BaseCommand(stdout=StringIO(), stderr=StringIO()).check()
            direct = run_checks(tags=[BOUNDARIES])

        assert probes.calls == ["next"]
        assert direct == []

    def test_a_check_already_in_the_registry_stays(self) -> None:
        probes = Probes()
        shared = probes.check("boundaries", BOUNDARIES)

        with held_checks([shared], area=[shared]):
            _output("--tag", BOUNDARIES)
            kept = shared in registry.registered_checks

        assert kept
        assert probes.calls == ["boundaries"]

    def test_the_real_checks_under_the_tag(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT), held_checks():
            output = _output("--tag", BOUNDARIES)

        assert f"({_W})" in output


class TestStrictOff:
    """In the strict mode boundaries without `BASE_DIR` are a warning."""

    def test_without_base_dir(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, framework=_STRICT, BASE_DIR=None):
            (message,) = boundary_checks.run_checks()

        assert type(message) is DjangoWarning
        assert message.id == STRICT_OFF
        assert message.msg == (
            "NEXT_FRAMEWORK['BOUNDARIES'] is 'strict', but the boundaries go "
            "unchecked, because BASE_DIR is not set."
        )
        assert message.hint == (
            "Point BASE_DIR at the folder that holds the project apps. "
            f"See {DOCTRINE}#installing."
        )

    def test_the_warn_mode_stays_quiet_without_base_dir(self, tmp_path) -> None:
        with boundary_project(tmp_path, _PROJECT, BASE_DIR=None):
            assert check_boundaries() == []


_CYCLE = {
    "alpha": {"services.py": "from beta.models import thing\n"},
    "beta": {"models.py": "thing = 1\n", "services.py": "import alpha.services\n"},
}


class TestCycleLevel:
    """A cycle warns in the warn mode and fails the check in the strict one, as E167."""

    def test_warn_mode(self, tmp_path) -> None:
        with boundary_project(tmp_path, _CYCLE), held_checks():
            messages = check_boundaries()
            call_command("check", "--tag", BOUNDARIES, stdout=StringIO())

        assert [(type(item), item.id, item.obj) for item in messages] == [
            (DjangoWarning, CYCLE, "alpha/services.py:1"),
            (DjangoWarning, CYCLE, "beta/services.py:1"),
        ]
        assert messages[0].hint == (
            "Cut one edge of the cycle, usually by moving the shared code down. The "
            f"strict mode fails the check on it. See {DOCTRINE}#no-cycles."
        )

    def test_strict_mode(self, tmp_path) -> None:
        with boundary_project(tmp_path, _CYCLE, framework=_STRICT), held_checks():
            messages = check_boundaries()
            with pytest.raises(SystemCheckError, match=r"next\.E167"):
                call_command("check", "--tag", BOUNDARIES, stdout=StringIO())

        assert [(type(item), item.id) for item in messages] == [
            (Error, CYCLE),
            (Error, CYCLE),
        ]

    @pytest.mark.parametrize("framework", [{}, _STRICT])
    def test_a_recorded_cycle_is_quiet_in_both_modes(self, tmp_path, framework) -> None:
        with boundary_project(tmp_path, _CYCLE, framework=framework):
            boundaries_manager.refreeze()
            boundaries_manager.reset()

            assert check_boundaries() == []


_BROKEN_DECLARATION = {
    "shop": {
        "apps.py": app_config("shop", depends_on=["crm"], isolated="yes"),
        "views.py": "from crm.utils import helper\n",
    },
    "crm": {
        "apps.py": app_config("crm", depends_on=("billing",)),
        "utils.py": "helper = 1\n",
    },
}


class TestCheckDeclarations:
    """A broken attribute is E175 and a missing app E174, both errors in any mode."""

    def test_the_messages(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv(ENVIRONMENT_VARIABLE, "config.cut")

        with boundary_project(tmp_path, _BROKEN_DECLARATION):
            messages = check_declarations()

        assert [(type(item), item.id, item.obj, item.msg) for item in messages] == [
            (
                Error,
                CLOSURE,
                "crm/apps.py:6",
                "crm depends on billing, which config.cut does not install.",
            ),
            (
                Error,
                DECLARATIONS,
                "shop/apps.py:6",
                (
                    "shop declares depends_on as something other than a tuple of app "
                    "names."
                ),
            ),
            (
                Error,
                DECLARATIONS,
                "shop/apps.py:7",
                "shop declares isolated as str instead of a bool.",
            ),
        ]
        assert messages[0].hint == (
            "Make each entry of depends_on the name of an app these settings "
            f"install, as the message says. See {DOCTRINE}#closure."
        )
        assert messages[1].hint == (
            "Fix the attribute on the AppConfig as the message says. See "
            f"{DOCTRINE}#declarations."
        )

    def test_without_grimp_next_to_the_findings(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(imports_module, "AVAILABLE", False)

        with boundary_project(tmp_path, _BROKEN_DECLARATION):
            ids = _own_ids()

        assert ids == [CLOSURE, DECLARATIONS, DECLARATIONS, _W]

    def test_the_ratchet_never_records_them(self, tmp_path) -> None:

        with boundary_project(tmp_path, _BROKEN_DECLARATION, framework=_STRICT):
            boundaries_manager.refreeze()
            boundaries_manager.reset()
            ids = _own_ids()

        assert (tmp_path / "boundaries.toml").read_text() == (
            'version = 1\n\n[[violation]]\nfile = "shop/views.py"\n'
            'code = "next.W136"\ncount = 1\n'
        )
        assert ids == [CLOSURE, DECLARATIONS, DECLARATIONS]


class TestDroppedApp:
    """A model relation into an app the settings drop is the native E307 of Django."""

    def test_the_check_fails(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(django_apps, "_pending_operations", defaultdict(list))
        apps = {
            "shop": {
                "apps.py": app_config("shop", depends_on=("crm",)),
                "models.py": (
                    "from django.db import models\n\n\n"
                    "class Order(models.Model):\n"
                    '    buyer = models.ForeignKey("crm.Client", '
                    "on_delete=models.CASCADE)\n"
                ),
            }
        }

        with boundary_project(tmp_path, apps), held_checks([check_lazy_references]):
            ids = {message.id for message in run_checks()}
            with pytest.raises(SystemCheckError, match=r"fields\.E307"):
                call_command("check", stdout=StringIO())
            with pytest.raises(SystemCheckError, match=r"next\.E174"):
                call_command("check", "--tag", BOUNDARIES, stdout=StringIO())

        assert "fields.E307" in ids
        assert CLOSURE not in ids


class TestRefusedSettings:
    """A wrong mode or graph key is named once under the tag, and nothing is scanned."""

    @pytest.mark.parametrize("mode", ["strct", True], ids=["word", "bool"])
    @pytest.mark.parametrize(
        "tags",
        [(), (BOUNDARIES,), (NEXT, BOUNDARIES)],
        ids=["no_tag", "boundaries", "next_and_boundaries"],
    )
    def test_a_wrong_mode(self, tmp_path, mode, tags) -> None:
        args = [item for tag in tags for item in ("--tag", tag)]

        with (
            boundary_project(tmp_path, _PROJECT, framework={"BOUNDARIES": mode}),
            held_checks([check_next_framework_boundaries_mode]),
            pytest.raises(SystemCheckError) as raised,
        ):
            _output(*args)

        assert str(raised.value).count("next.E172") == 1
        assert _W not in str(raised.value)

    def test_a_graph_key_that_is_no_path(self, tmp_path) -> None:
        framework = {"BOUNDARIES_IMPORT_GRAPH": 42}

        with (
            boundary_project(tmp_path, _PROJECT, framework=framework),
            held_checks([check_next_framework_boundaries_mode]),
            pytest.raises(SystemCheckError) as raised,
        ):
            _output("--tag", NEXT, "--tag", BOUNDARIES)

        assert str(raised.value).count("BOUNDARIES_IMPORT_GRAPH") == 1
        assert (
            "NEXT_FRAMEWORK['BOUNDARIES_IMPORT_GRAPH'] is 42, which is no dotted path "
            "to a class."
        ) in str(raised.value)
        assert _W not in str(raised.value)


class TestAnotherCheckCommand:
    """A `check` command of an app before `next` leaves the tag unknown, loudly."""

    def test_the_tag_is_refused(self, tmp_path) -> None:
        vendor = tmp_path / "vendor"
        commands = vendor / "othercheck" / "management" / "commands"
        commands.mkdir(parents=True)
        for folder in (commands, commands.parent, commands.parent.parent):
            (folder / "__init__.py").write_text("")
        (commands / "check.py").write_text(
            "from django.core.management.commands.check import Command\n"
        )

        with (
            boundary_project(tmp_path / "project", _PROJECT),
            held_checks([check_next_framework_boundaries_mode]),
            importable_dir(vendor),
            override_settings(
                INSTALLED_APPS=["othercheck", *django_settings.INSTALLED_APPS]
            ),
        ):
            get_commands.cache_clear()
            try:
                with pytest.raises(CommandError, match='"boundaries" tag'):
                    _output("--tag", BOUNDARIES)
            finally:
                get_commands.cache_clear()
