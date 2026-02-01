from typing import ClassVar

from django.contrib import messages
from django.http import HttpRequest, HttpResponseRedirect
from todos.models import Todo

from next import forms
from next.pages import context


@context("todos")
def get_todos(_request: HttpRequest) -> list[Todo]:
    """Get all todos for the list."""
    return list(Todo.objects.all())


class TodoForm(forms.ModelForm):
    """Form for creating and editing todos."""

    class Meta:
        model = Todo
        fields: ClassVar[list[str]] = ["title", "description", "is_completed"]

    title = forms.CharField(max_length=200)
    description = forms.CharField(widget=forms.Textarea, required=False)
    is_completed = forms.BooleanField(required=False)

    @classmethod
    def get_initial(cls, _request: HttpRequest) -> dict:
        """Get initial data from request (empty for new todos)."""
        return {}


@forms.action("create_todo", form_class=TodoForm)
def create_todo_handler(request: HttpRequest, form: TodoForm) -> HttpResponseRedirect:
    """Create a new todo and redirect to home."""
    form.save()
    messages.success(request, "Todo created successfully.")
    return HttpResponseRedirect("/")
