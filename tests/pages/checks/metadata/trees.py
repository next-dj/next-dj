from pathlib import Path

from tests.support.trees import templated_page


DESCRIPTION = "A description long enough to pass the audit without any complaint."


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


def metadata_page(directory: Path, metadata: str) -> Path:
    """Write a templated page whose `page.py` holds the `metadata` dict literal."""
    return templated_page(directory, f"metadata = {metadata}\n")


def scope(**metadata: object) -> dict[str, object]:
    """Return the `NEXT_FRAMEWORK` mapping carrying `metadata` as its `METADATA`."""
    return {"METADATA": metadata}
