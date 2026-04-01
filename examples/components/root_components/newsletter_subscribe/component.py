from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponseRedirect

from next import forms


class NewsletterForm(forms.Form):
    """Footer newsletter signup."""

    email = forms.EmailField(
        label="Enter your email to subscribe to our newsletter",
        max_length=254,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control form-control-sm",
                "placeholder": "Your email",
                "autocomplete": "email",
            },
        ),
    )

    @classmethod
    def get_initial(cls, _request: HttpRequest) -> dict:
        """Return empty initial data."""
        return {}


@forms.action("newsletter_subscribe", form_class=NewsletterForm)
def newsletter_subscribe_handler(
    request: HttpRequest,
    form: NewsletterForm,
) -> HttpResponseRedirect:
    _ = form.cleaned_data["email"]
    messages.success(request, "Thanks — you're on the list.")
    return HttpResponseRedirect(request.META.get("HTTP_REFERER", "/"))
