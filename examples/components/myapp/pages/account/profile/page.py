from typing import ClassVar

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.http import HttpRequest, HttpResponseRedirect

from next import forms


User = get_user_model()


class AccountProfileForm(forms.ModelForm):
    """Edit the authenticated user's first and last name."""

    class Meta:
        model = User
        fields = ("first_name", "last_name")
        widgets: ClassVar[dict[str, forms.Widget]] = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
        }

    @classmethod
    def get_initial(cls, request: HttpRequest) -> User | dict:
        """Bind to the logged-in user for ModelForm instance=."""
        if request.user.is_authenticated:
            return request.user
        return {}


@forms.action("update_profile", form_class=AccountProfileForm)
def update_profile_handler(
    form: AccountProfileForm, request: HttpRequest
) -> HttpResponseRedirect:
    if not request.user.is_authenticated:
        return HttpResponseRedirect(settings.LOGIN_URL)
    form.save()
    messages.success(request, "Profile updated.")
    return HttpResponseRedirect("/account/profile/")
