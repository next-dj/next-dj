import ast
from pathlib import Path

import pytest
from django.core.management import call_command

from next import conf


TEMPLATE = Path(conf.__file__).parent / "app_template"
EXAMPLE = Path(__file__).resolve().parents[1] / "myapp"
STAND_IN = "syncapp"


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
