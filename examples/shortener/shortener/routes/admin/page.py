from django.http import HttpRequest
from shortener.cache import pending_clicks
from shortener.models import Link

from next.forms import ModelForm
from next.pages import context


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
