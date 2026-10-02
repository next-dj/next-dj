from pathlib import Path
from unittest.mock import patch

import pytest

from next.checks import reset_check_caches
from next.scripts.checks import (
    check_consented_needs_consent,
    check_gated_blocking_scripts,
    check_script_categories,
    check_script_declarations,
    check_script_deploy,
    check_script_names,
    check_scripts_need_the_runtime,
    check_scripts_sources,
)
from tests.scripts.trees import write_tree
from tests.support import check_ids, routed, write_page


HEADER = "from next.scripts import Script, Strategy\nscripts = (\n"
CONSENTED = '{% #consented "ads" %}<video>{% /consented %}'


def _scripts(*declarations: str) -> str:
    return HEADER + "".join(f"    {item},\n" for item in declarations) + ")\n"


def _run(tmp_path: Path, check, scripts: str, **framework: object):
    root = write_tree(tmp_path / "pages", scripts=scripts)
    with routed(root, **framework):
        return check()


class TestSources:
    """A `scripts.py` imports and declares an iterable of `Script` (`next.E133`)."""

    def test_a_broken_source_is_e133(self, tmp_path: Path) -> None:
        messages = _run(tmp_path, check_scripts_sources, "raise RuntimeError('x')\n")
        assert check_ids(messages) == ["next.E133"]
        assert "failed to import" in messages[0].msg

    def test_a_stray_value_is_e133(self, tmp_path: Path) -> None:
        messages = _run(tmp_path, check_scripts_sources, "scripts = 3\n")
        assert check_ids(messages) == ["next.E133"]
        assert "scripts = (Script(...), ...)" in messages[0].msg

    def test_a_valid_source_is_silent(self, tmp_path: Path) -> None:
        assert (
            _run(tmp_path, check_scripts_sources, _scripts("Script('a', init='1')"))
            == []
        )


class TestNames:
    """A tree names each script once (`next.E134`)."""

    def test_a_repeated_name_is_e134(self, tmp_path: Path) -> None:
        scripts = _scripts("Script('a', init='1')", "Script('a', init='2')")
        messages = _run(tmp_path, check_script_names, scripts)
        assert check_ids(messages) == ["next.E134"]


class TestCategories:
    """A usable category list (`next.E135`) holding every script category (`E140`)."""

    def test_an_unlisted_category_is_e140(self, tmp_path: Path) -> None:
        scripts = _scripts("Script('a', init='1', category='ads')")
        messages = _run(tmp_path, check_script_categories, scripts)
        assert check_ids(messages) == ["next.E140"]
        assert "'ads'" in messages[0].msg

    @pytest.mark.parametrize(
        ("categories", "fragment"),
        [(["analytics"], "does not list 'necessary'"), ("x", "lists no category")],
    )
    def test_a_list_without_necessary_is_e135(
        self, tmp_path: Path, categories: object, fragment: str
    ) -> None:
        messages = _run(
            tmp_path,
            check_script_categories,
            _scripts("Script('a', init='1')"),
            CONSENT={"CATEGORIES": categories},
        )
        assert check_ids(messages) == ["next.E135"]
        assert fragment in messages[0].msg

    def test_a_name_the_cookie_cannot_carry_is_e146(self, tmp_path: Path) -> None:
        listed = [
            "necessary",
            "ad_storage",
            "web-v2.1",
            "a,b",
            "a:b",
            "a;b",
            "a b",
            "é",
        ]
        messages = _run(
            tmp_path,
            check_script_categories,
            _scripts("Script('a', init='1')"),
            CONSENT={"CATEGORIES": listed},
        )
        assert check_ids(messages) == ["next.E146"] * 5
        assert "'a,b'" in messages[0].msg
        assert "letters, digits" in messages[0].msg


class TestDeclarations:
    """A script declares something to run and parts it can carry, one id each."""

    @pytest.mark.parametrize(
        ("declaration", "check_id", "fragment"),
        [
            ("Script('a')", "next.E136", "carries neither src nor init"),
            ("Script('a', src='ftp://x.example/a.js')", "next.E141", "a URL"),
            ("Script('a', src='/abs/a.js')", "next.E141", "not a staticfiles name"),
            ("Script('a', src='../a.js')", "next.E141", "outside the static root"),
            ("Script('a', src='missing/a.js')", "next.E141", "no finder answers"),
            ("Script('a', init='x(\"</script>\")')", "next.E142", "holds </script"),
            ("Script('a', init='x(\"<!--\")')", "next.E142", "<!-- in its init"),
            (
                "Script('a', init='1', attrs={'onload': 'x'})",
                "next.E143",
                "attributes onload",
            ),
            (
                "Script('a', init='1', strategy='soon')",
                "next.E144",
                "names the strategy 'soon'",
            ),
        ],
    )
    def test_an_unusable_declaration_names_its_problem(
        self, tmp_path: Path, declaration: str, check_id: str, fragment: str
    ) -> None:
        messages = _run(tmp_path, check_script_declarations, _scripts(declaration))
        assert check_ids(messages) == [check_id]
        assert fragment in messages[0].msg

    def test_https_and_a_found_name_are_silent(self, tmp_path: Path) -> None:
        scripts = _scripts(
            "Script('a', src='https://cdn.example/a.js')",
            "Script('b', src='http://cdn.example/b.js')",
            "Script('c', src='found/c.js')",
        )
        with patch("next.scripts.checks.finders.find", return_value="/found/c.js"):
            assert _run(tmp_path, check_script_declarations, scripts) == []


class TestRuntime:
    """A script only the runtime loads needs the runtime injected (`next.E138`)."""

    def test_a_disabled_runtime_is_e138(self, tmp_path: Path) -> None:
        scripts = _scripts(
            "Script('a', init='1')",
            "Script('b', init='1', strategy=Strategy.IDLE)",
            "Script('c', init='1', category='marketing')",
        )
        messages = _run(
            tmp_path,
            check_scripts_need_the_runtime,
            scripts,
            NEXT_JS_OPTIONS={"policy": "disabled"},
        )
        assert check_ids(messages) == ["next.E138", "next.E138"]

    def test_an_injected_runtime_is_silent(self, tmp_path: Path) -> None:
        scripts = _scripts("Script('b', init='1', strategy=Strategy.IDLE)")
        assert _run(tmp_path, check_scripts_need_the_runtime, scripts) == []


class TestGatedBlocking:
    """A gated blocking script blocks nothing once the runtime renders it (W125)."""

    def test_it_is_w125(self, tmp_path: Path) -> None:
        scripts = _scripts(
            "Script('a', init='1', category='marketing', strategy=Strategy.BLOCKING)"
        )
        assert check_ids(_run(tmp_path, check_gated_blocking_scripts, scripts)) == [
            "next.W125"
        ]

    def test_an_always_server_render_is_silent(self, tmp_path: Path) -> None:
        scripts = _scripts(
            "Script('a', init='1', category='marketing', strategy=Strategy.BLOCKING)"
        )
        messages = _run(
            tmp_path,
            check_gated_blocking_scripts,
            scripts,
            CONSENT={"SERVER_RENDER": True},
        )
        assert messages == []


class TestDeploy:
    """A deploy loads every script over https (W127)."""

    def test_plain_http_warns(self, tmp_path: Path) -> None:
        scripts = _scripts(
            "Script('a', src='http://cdn.example/a.js')",
            "Script('ok', src='https://cdn.example/b.js')",
            "Script('inline', init='1')",
        )
        messages = _run(tmp_path, check_script_deploy, scripts)
        assert check_ids(messages) == ["next.W127"]
        assert "'a'" in messages[0].msg


@pytest.fixture()
def _fresh_run():
    reset_check_caches()
    yield
    reset_check_caches()


def _consented_run(root: Path, **framework: object):
    with routed(root, **framework):
        return check_consented_needs_consent()


@pytest.mark.usefixtures("_fresh_run")
class TestConsentedNeedsConsent:
    """`{% #consented %}` without a `CONSENT` entry warns (`next.W133`)."""

    def test_a_consented_block_without_consent_is_w133(self, tmp_path: Path) -> None:
        root = write_tree(tmp_path / "pages", scripts=None)
        for index in range(4):
            write_page(root, f"p{index}", f"template = {CONSENTED!r}\n")
        [warning] = _consented_run(root)
        assert warning.id == "next.W133"
        assert "and 1 more" in warning.msg
        assert "NEXT_FRAMEWORK['CONSENT']" in warning.msg

    def test_a_consent_entry_is_silent(self, tmp_path: Path) -> None:
        root = write_tree(
            tmp_path / "pages", scripts=None, page=f"template = {CONSENTED!r}\n"
        )
        assert _consented_run(root, CONSENT={}) == []

    def test_pages_without_the_block_are_silent(self, tmp_path: Path) -> None:
        root = write_tree(tmp_path / "pages", scripts=None)
        assert _consented_run(root) == []
