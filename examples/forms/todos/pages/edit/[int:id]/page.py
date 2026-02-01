from typing import ClassVar

from django.contrib import messages
from django.http import Http404, HttpRequest, HttpResponseRedirect
from todos.models import Todo

from next import forms
from next.pages import context


@context("todo")
def get_todo(_request: HttpRequest, id: int, **_kwargs: object) -> Todo:  # noqa: A002
    """Get todo by ID or raise 404."""
    try:
        return Todo.objects.get(pk=id)
    except Todo.DoesNotExist as err:
        msg = "Todo not found"
        raise Http404(msg) from err


class TodoEditForm(forms.ModelForm):
    """Form for editing todos."""

    class Meta:
        model = Todo
        fields: ClassVar[list[str]] = ["title", "description", "is_completed"]

    title = forms.CharField(max_length=200)
    description = forms.CharField(widget=forms.Textarea, required=False)
    is_completed = forms.BooleanField(required=False)

    @classmethod
    def get_initial(
        cls,
        _request: HttpRequest,
        id: int,  # noqa: A002
        **_kwargs: object,
    ) -> Todo | dict:
        """Get todo instance for editing.

        The `id` parameter is automatically passed from the URL pattern [int:id].
        Returns the Todo model instance, which will be used as the `instance`
        parameter when creating the form (for ModelForm).
        """
        try:
            return Todo.objects.get(pk=id)
        except Todo.DoesNotExist:
            return {}


@forms.action("update_todo", form_class=TodoEditForm)
def update_todo_handler(
    request: HttpRequest, form: TodoEditForm, **_kwargs: object
) -> HttpResponseRedirect:
    """Update todo and redirect to home.

    The `id` parameter is automatically passed from the URL pattern [int:id].
    The form instance is already set from get_initial, so we just save it.
    """
    form.save()
    messages.success(request, "Todo updated successfully.")
    return HttpResponseRedirect("/", preserve_request=request)
