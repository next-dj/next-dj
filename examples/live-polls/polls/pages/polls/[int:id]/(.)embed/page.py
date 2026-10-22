from django.http import HttpRequest
from polls.models import Poll

from next import context, page
from next.pages import MetadataDict


@context("embed_code")
def embed_code(request: HttpRequest, poll: Poll) -> str:
    """Return the iframe snippet that puts the live widget on another site."""
    url = request.build_absolute_uri(f"/polls/{poll.pk}/embed/")
    return f'<iframe src="{url}" width="480" height="360" loading="lazy"></iframe>'


@page.metadata
def embed_code_meta(poll: Poll) -> MetadataDict:
    """Title the tab after the poll while the snippet covers the vote page."""
    return {"title": f"Embed · {poll.question}"}
