from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {
    "title": "Routing",
    "description": "How file paths become URL patterns and how database rows join in.",
}


@context("section")
def section() -> str:
    """Return the identifier used by the layout to mark the active doc section."""
    return "routing"
