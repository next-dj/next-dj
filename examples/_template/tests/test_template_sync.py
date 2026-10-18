import ast
from pathlib import Path

import pytest
from django.core.management import call_command

from next import conf


TEMPLATE = Path(conf.__file__).parent / "app_template"
PROJECT_TEMPLATE = Path(conf.__file__).parent / "project_template"
EXAMPLE = Path(__file__).resolve().parents[1] / "myapp"
HOME = Path(__file__).resolve().parents[1] / "home"
STAND_IN = "syncapp"
PROJECT_STAND_IN = "syncproject"


def _normalised(source: str) -> tuple[list[str], list[str]]:
    body = ast.parse(source).body
    imports = sorted(
        ast.dump(node) for node in body if isinstance(node, ast.Import | ast.ImportFrom)
    )
    rest = [
        ast.dump(node)
        for node in body
        if not isinstance(node, ast.Import | ast.ImportFrom)
    ]
    return imports, rest


def _shared_modules(generated: Path) -> list[str]:
    ours = {path.relative_to(EXAMPLE).as_posix() for path in EXAMPLE.rglob("*.py")}
    theirs = {
        path.relative_to(generated).as_posix() for path in generated.rglob("*.py")
    }
    return sorted(ours & theirs)


@pytest.fixture(scope="module")
def generated(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("generated")
    call_command("startapp", STAND_IN, str(target), template=str(TEMPLATE))
    return target


@pytest.fixture(scope="module")
def generated_home(tmp_path_factory) -> Path:
    target = tmp_path_factory.mktemp("project")
    call_command(
        "startproject", PROJECT_STAND_IN, str(target), template=str(PROJECT_TEMPLATE)
    )
    return target / "home"


def test_the_example_holds_every_template_module_but_its_tests(generated) -> None:
    modules = {
        path.relative_to(generated).as_posix() for path in generated.rglob("*.py")
    }
    app_modules = {name for name in modules if not name.startswith("tests/")}

    assert _shared_modules(generated) == sorted(app_modules)


def test_every_shared_module_matches_the_template(generated) -> None:
    for name in _shared_modules(generated):
        rendered = (generated / name).read_text().replace(STAND_IN, "myapp")
        rendered = rendered.replace(STAND_IN.capitalize(), "Myapp")
        example = (EXAMPLE / name).read_text()

        assert _normalised(rendered) == _normalised(example), name


def test_the_home_app_matches_the_project_template(generated_home) -> None:
    modules = sorted(
        path.relative_to(generated_home).as_posix()
        for path in generated_home.rglob("*.py")
    )
    ours = sorted(path.relative_to(HOME).as_posix() for path in HOME.rglob("*.py"))

    assert modules == ours
    for name in modules:
        rendered = (generated_home / name).read_text()
        example = (HOME / name).read_text()

        assert _normalised(rendered) == _normalised(example), name
    assert (generated_home / "pages" / "template.djx").read_text() == (
        HOME / "pages" / "template.djx"
    ).read_text()
