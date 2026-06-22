from django.http import Http404, HttpRequest, HttpResponse
from shortener.cache import pending_clicks
from shortener.models import Link

from next.forms import ModelForm, action
from next.pages import context
from next.partial import Patches


@context("recent_links", inherit_context=True)
def recent_links() -> list[Link]:
    return list(Link.objects.order_by("-clicks", "-created_at")[:10])


context("pending_clicks")(pending_clicks)


class EditLinkForm(ModelForm):
    class Meta:
        model = Link
        fields = ("url",)
        success_url = "/admin/"
        success_message = "Destination updated."

    @classmethod
    def get_initial(cls, request: HttpRequest) -> Link | None:
        """Resolve the edited link from the posted slug, None on a plain render."""
        slug = request.POST.get("slug")
        if not slug:
            return None
        return Link.objects.filter(slug=slug).first()


@action("delete_link")
def delete_link(request: HttpRequest) -> HttpResponse:
    """Delete the posted link and drop its row from the list in place.

    A live runtime removes the addressed row by its slug key without a
    reload. Without the runtime the builder falls back to a redirect to
    the admin index so the no-JS path re-renders the trimmed list.
    """
    slug = request.POST.get("slug", "")
    deleted, _ = Link.objects.filter(slug=slug).delete()
    if not deleted:
        raise Http404
    return (
        Patches(request)
        .remove({"css": f'li[data-next-key="{slug}"]'})
        .response(fallback="/admin/")
    )
