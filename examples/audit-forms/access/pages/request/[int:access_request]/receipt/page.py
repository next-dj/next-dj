from access.models import AccessRequest
from django.http import Http404, HttpRequest, HttpResponse

from next import context, page
from next.deps import DObject
from next.pages import MetadataDict


RECEIPT = """
<div class="mx-auto max-w-xl space-y-6" data-receipt>
  <a href="{% url 'next:page_request_int_access_request_audit' access_request=pk %}"
     class="text-xs text-muted-foreground hover:text-foreground">← Back to the trail</a>
  {% component "page_header" eyebrow="Access request receipt" title=receipt_number %}
  <dl class="grid grid-cols-[8rem_1fr] gap-2 rounded-md border bg-card p-6 text-sm">
    {% for label, value in receipt %}
      <dt class="text-muted-foreground">{{ label }}</dt>
      <dd class="whitespace-pre-line">{{ value }}</dd>
    {% endfor %}
  </dl>
  <a href="?format=txt" class="text-sm font-medium underline">Download as text</a>
</div>
"""


def render(
    request: HttpRequest, access_request: DObject[AccessRequest]
) -> str | HttpResponse:
    """Serve the receipt of a filed request, as a text download on `?format=txt`.

    The string is template source, so request data reaches it through context only.
    """
    if not access_request.is_filed():
        msg = "No receipt before the backend records the filing."
        raise Http404(msg)
    if request.GET.get("format") != "txt":
        return RECEIPT
    response = HttpResponse(
        access_request.receipt_text(), content_type="text/plain; charset=utf-8"
    )
    filename = f"access-request-{access_request.pk}.txt"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@context("pk")
def pk(access_request: DObject[AccessRequest]) -> int:
    return access_request.pk


@context("receipt")
def receipt(access_request: DObject[AccessRequest]) -> list[tuple[str, str]]:
    return access_request.receipt_lines()


@context("receipt_number")
def receipt_number(access_request: DObject[AccessRequest]) -> str:
    return access_request.receipt_number


@page.metadata
def receipt_meta(access_request: DObject[AccessRequest]) -> MetadataDict:
    return {
        "title": f"Receipt · Request #{access_request.pk}",
        "robots": {"index": False},
    }
