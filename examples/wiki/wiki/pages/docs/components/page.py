from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {
    "title": "Components",
    "description": "Composite components with co-located assets and a children body.",
}


@context("markup_sample")
def markup_sample() -> str:
    """Return the snippet the page sends through the child and the prop channel."""
    return "<em>emphasis</em>"
