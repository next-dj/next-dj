import ast
from pathlib import Path
from typing import NamedTuple


ROOT = Path(__file__).resolve().parent.parent

# Members whose value is not the lower-cased name, each with why. The list only shrinks.
KNOWN: dict[tuple[str, str], str] = {}


class Member(NamedTuple):
    """One member of a `StrEnum` in `next/`, with the literal it is set to."""

    path: str
    enum: str
    name: str
    value: object

    @property
    def key(self) -> tuple[str, str]:
        return self.path, f"{self.enum}.{self.name}"

    def describe(self) -> str:
        return (
            f"{self.path} {self.enum}.{self.name} = {self.value!r}, "
            f"write {self.name.lower()!r} instead."
        )


def _is_str_enum(node: ast.ClassDef) -> bool:
    return any(ast.unparse(base).split(".")[-1] == "StrEnum" for base in node.bases)


def members() -> list[Member]:
    """Return every member a `StrEnum` class body under `next/` assigns."""
    found = []
    for path in sorted((ROOT / "next").rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.ClassDef) and _is_str_enum(node)):
                continue
            for statement in node.body:
                if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
                    target = statement.targets[0]
                    if isinstance(target, ast.Name):
                        value = statement.value
                        literal = (
                            value.value if isinstance(value, ast.Constant) else value
                        )
                        found.append(Member(relative, node.name, target.id, literal))
    return found


def report(found: list[Member], known: dict[tuple[str, str], str]) -> list[str]:
    """Name every member off the rule and every known exception that no longer is."""
    off = [member for member in found if member.value != member.name.lower()]
    seen = {member.key for member in off}
    gone = [
        f"{key} follows the rule now, so delete it from KNOWN in tests/test_enums.py."
        for key in sorted(known.keys() - seen)
    ]
    return [*(member.describe() for member in off if member.key not in known), *gone]


class TestStrEnumValues:
    """The value of every `StrEnum` member in `next/` is its name in lower case."""

    def test_the_scan_reads_the_enums(self) -> None:
        keys = {member.key for member in members()}
        assert ("next/defer/markers.py", "LaneRule.CONNECTION_STATE") in keys
        assert ("next/conf/router.py", "RouterScope.OPT_IN") in keys

    def test_every_value_is_the_lower_cased_name(self) -> None:
        assert report(members(), KNOWN) == []

    def test_a_value_off_the_rule_is_named(self) -> None:
        found = [Member("next/x.py", "Mode", "OPT_IN", "opt-in")]
        assert report(found, {}) == [
            "next/x.py Mode.OPT_IN = 'opt-in', write 'opt_in' instead."
        ]

    def test_a_known_exception_that_follows_the_rule_is_named(self) -> None:
        found = [Member("next/x.py", "Codec", "BR", "br")]
        stale = (
            "('next/x.py', 'Codec.BR') follows the rule now, so delete it from KNOWN "
            "in tests/test_enums.py."
        )
        assert report(found, {("next/x.py", "Codec.BR"): "a codec token"}) == [stale]
