import pytest

from next.boundaries.rules import ISOLATED_SERVICE, PRIVATE_IMPORT, UNANNOTATED_SERVICE
from tests.support.boundaries import app_config, scanned
from tests.support.cases.boundaries import (
    ISO_BROKEN,
    ISO_MODELS,
    ISO_SHARED,
    SERVICE_FINE_CASES,
    SERVICE_LEAK_CASES,
    ServiceCase,
)


_SERVICE_CODES = (ISOLATED_SERVICE, UNANNOTATED_SERVICE)


def _iso(
    files: dict[str, str | bytes], *, isolated: bool = True
) -> dict[str, dict[str, str]]:
    return {
        "iso": {
            "apps.py": app_config("iso", isolated=isolated),
            "models.py": ISO_MODELS,
            "shared.py": ISO_SHARED,
            "lib/broken.py": ISO_BROKEN,
            **files,
        }
    }


def _service_rows(tmp_path, files, **options) -> list[tuple[str, int, str, str]]:
    scan = scanned(tmp_path, _iso(files, **options))
    return [
        (finding.file, finding.line, finding.code, finding.message)
        for finding in scan.findings
        if finding.code in _SERVICE_CODES
    ]


def _module(case: ServiceCase) -> str:
    return f"{case.source}\n\ndef fetch() -> {case.returns}:\n    pass\n"


def _line(case: ServiceCase) -> int:
    return len(case.source.splitlines()) + 3


class TestReturnTypes:
    """A public service of an isolated app returns no model, queryset or loose type."""

    @pytest.mark.parametrize("case", SERVICE_LEAK_CASES, ids=lambda case: case.id)
    def test_a_leak(self, tmp_path, case: ServiceCase) -> None:
        text = _module(case)

        assert _service_rows(tmp_path, {"services.py": text}) == [
            (
                "iso/services.py",
                _line(case),
                ISOLATED_SERVICE,
                f"The service fetch of the isolated app iso returns {case.reason}.",
            )
        ]

    @pytest.mark.parametrize("case", SERVICE_FINE_CASES, ids=lambda case: case.id)
    def test_a_fine_type(self, tmp_path, case: ServiceCase) -> None:
        assert _service_rows(tmp_path, {"services.py": _module(case)}) == []


class TestServices:
    """Only the public top level functions of the services of an isolated app count."""

    def test_no_annotation(self, tmp_path) -> None:
        text = "def fetch():\n    pass\n\n\nasync def load():\n    pass\n"

        assert _service_rows(tmp_path, {"services.py": text}) == [
            (
                "iso/services.py",
                1,
                UNANNOTATED_SERVICE,
                "The service fetch of the isolated app iso has no return annotation.",
            ),
            (
                "iso/services.py",
                5,
                UNANNOTATED_SERVICE,
                "The service load of the isolated app iso has no return annotation.",
            ),
        ]

    def test_an_async_service(self, tmp_path) -> None:
        text = (
            "from iso.models import Record\n\n\nasync def load() -> Record:\n    pass\n"
        )

        assert _service_rows(tmp_path, {"services.py": text}) == [
            (
                "iso/services.py",
                4,
                ISOLATED_SERVICE,
                (
                    "The service load of the isolated app iso returns the model "
                    "iso.models.Record."
                ),
            )
        ]

    def test_private_functions_and_methods_are_not_read(self, tmp_path) -> None:
        text = (
            "from iso.models import Record\n\n\n"
            "def _fetch() -> Record:\n    pass\n\n\n"
            "class Reader:\n    def fetch(self) -> Record:\n        pass\n\n\n"
            "fetch = lambda: None\n"
        )

        assert _service_rows(tmp_path, {"services.py": text}) == []

    def test_a_name_bound_to_a_function(self, tmp_path) -> None:
        text = (
            "from iso.models import Record\n\n\n"
            "def _get() -> Record:\n    pass\n\n\n"
            "get = _get\n_hidden = _get\nother = missing\n"
        )

        assert _service_rows(tmp_path, {"services.py": text}) == [
            (
                "iso/services.py",
                8,
                ISOLATED_SERVICE,
                (
                    "The service get of the isolated app iso returns the model "
                    "iso.models.Record."
                ),
            )
        ]

    def test_a_services_package(self, tmp_path) -> None:
        files = {
            "services/ping.py": "def ping():\n    pass\n",
            "services/query.py": (
                "from iso.models import Record\n\n\n"
                "def records() -> list[Record]:\n    pass\n"
            ),
            "services/broken.py": ISO_BROKEN,
            "helpers.py": "def leak():\n    pass\n",
        }

        assert [row[:3] for row in _service_rows(tmp_path, files)] == [
            ("iso/services/ping.py", 1, UNANNOTATED_SERVICE),
            ("iso/services/query.py", 4, ISOLATED_SERVICE),
        ]

    def test_a_services_package_with_its_init(self, tmp_path) -> None:
        files = {
            "services/__init__.py": "from iso.models import Record\n\n\ndef one() -> Record:\n    pass\n",
            "services/query.py": "def records() -> int:\n    pass\n",
        }

        assert [row[:3] for row in _service_rows(tmp_path, files)] == [
            ("iso/services/__init__.py", 4, ISOLATED_SERVICE)
        ]

    def test_an_app_that_is_not_isolated(self, tmp_path) -> None:
        text = "from iso.models import Record\n\n\ndef fetch() -> Record:\n    pass\n"

        assert _service_rows(tmp_path, {"services.py": text}, isolated=False) == []


_LEAKING_SERVICES = {
    "services/__init__.py": (
        "from iso.models import Record as Record\n\n\ndef fine() -> int:\n    pass\n"
    ),
    "services/query.py": (
        "from django.db.models import QuerySet\n\n\nclass Rows(QuerySet):\n    pass\n"
    ),
    "views.py": "from iso.services import Record\n",
}

_CONSUMER = {
    "leak.py": (
        "from iso.services import Record, fine\n"
        "from iso.services.query import Rows\n"
        "from iso.services import query\n"
        "from iso.services import *\n"
        "from iso import services\n"
    ),
    "scripts/run.py": "from ... import anything\n",
}


class TestReexports:
    """Another app may not take a model or a queryset from the isolated services."""

    def test_a_model_and_a_queryset_from_the_services(self, tmp_path) -> None:
        apps = {**_iso(_LEAKING_SERVICES), "shop": _CONSUMER}

        scan = scanned(tmp_path, apps)

        assert [
            (finding.file, finding.line, finding.app, finding.message)
            for finding in scan.findings
            if finding.code == PRIVATE_IMPORT
        ] == [
            (
                "shop/leak.py",
                1,
                "shop",
                (
                    "shop imports Record from iso.services, and Record is a model of "
                    "the isolated app iso, ask its services for a dataclass."
                ),
            ),
            (
                "shop/leak.py",
                2,
                "shop",
                (
                    "shop imports Rows from iso.services.query, and Rows is a queryset "
                    "or manager of the isolated app iso, ask its services for a "
                    "dataclass."
                ),
            ),
        ]

    def test_services_of_an_app_that_is_not_isolated(self, tmp_path) -> None:
        apps = {**_iso(_LEAKING_SERVICES, isolated=False), "shop": _CONSUMER}

        scan = scanned(tmp_path, apps)

        assert [f for f in scan.findings if f.code == PRIVATE_IMPORT] == []
