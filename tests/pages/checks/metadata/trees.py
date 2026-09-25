from pathlib import Path

from tests.support import file_router_config_entry, write_page


BASE = "https://acme.example"


DESCRIPTION = "A description long enough to pass the audit without any complaint."


I18N = {
    "USE_I18N": True,
    "LANGUAGES": [("en", "English"), ("de", "German")],
    "LANGUAGE_CODE": "en",
}


COMPONENTS = [
    {
        "BACKEND": "next.components.FileComponentsBackend",
        "DIRS": [],
        "COMPONENTS_DIR": "_components",
    }
]


NAMED_CALLABLE = """
from next.pages import page


@page.metadata
def metadata() -> dict:
    return {"title": "Named"}
"""


INHERITED_CALLABLE = """
from next.pages import page


@page.metadata(inherit=True)
def meta() -> dict:
    return {"title": "Inherited"}
"""


def templated_page(directory: Path, source: str, body: str | None = "<p>x</p>") -> Path:
    """Write a `page.py` of `source` in `directory` beside a `template.djx` of `body`."""
    return write_page(directory, source=source, body=body)


def metadata_page(directory: Path, metadata: str) -> Path:
    """Write a templated page whose `page.py` holds the `metadata` dict literal."""
    return templated_page(directory, f"metadata = {metadata}\n")


def scope(**metadata: object) -> dict[str, object]:
    """Return the `NEXT_FRAMEWORK` mapping carrying `metadata` as its `METADATA`."""
    return {"METADATA": metadata}


def framework(pages: Path) -> dict[str, object]:
    """Return a `NEXT_FRAMEWORK` routing `pages` with file components enabled."""
    return {
        "PAGE_BACKENDS": [file_router_config_entry(pages_dir=pages)],
        "COMPONENT_BACKENDS": COMPONENTS,
    }
