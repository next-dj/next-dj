from pathlib import Path

import pytest
from tests.e2e.harness_cases import DELIBERATE_FAILURE


pytest_plugins = ["pytester"]

pytestmark = pytest.mark.e2e

EXAMPLE = Path(__file__).resolve().parents[2]
CASES = Path(__file__).with_name("harness_cases.py")
HOME = Path.home()


@pytest.fixture()
def run(pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch) -> pytest.RunResult:
    monkeypatch.setenv("HOME", str(HOME))
    return pytester.runpytest_subprocess(
        str(CASES),
        "-c",
        str(EXAMPLE / "pytest.ini"),
        "--rootdir",
        str(EXAMPLE),
        "-p",
        "e2e_support.browser",
        "-p",
        "no:cacheprovider",
        "-p",
        "no:randomly",
        "--no-cov",
        "--setup-show",
        "--screenshot=only-on-failure",
        f"--output={pytester.path / 'artefacts'}",
        timeout=180,
    )


def test_a_failing_case_reports_its_own_error_and_finalises_every_fixture(
    run: pytest.RunResult,
) -> None:
    run.assert_outcomes(passed=1, failed=1, errors=0)
    output = run.stdout.str()
    assert DELIBERATE_FAILURE in output
    assert "kept a task running" not in output
    assert output.count("TEARDOWN F _shared_database") == 2
    assert output.count("TEARDOWN F page") == 2
