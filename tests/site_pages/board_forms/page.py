from django import forms
from django.http import HttpRequest, HttpResponse

from next.forms import Form


class RenameBoardForm(Form):
    """First of three neighbouring settings forms."""

    title = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the rename and fall back to the default redirect."""
        return None


class CreateColumnForm(Form):
    """Second neighbouring form that must stay untouched on rename errors."""

    column = forms.CharField(max_length=100)

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the column and fall back to the default redirect."""
        return None


class ArchiveBoardForm(Form):
    """Third neighbouring form on the same settings page."""

    confirm = forms.BooleanField()

    def on_valid(self, request: HttpRequest) -> HttpResponse | None:
        """Accept the archive and fall back to the default redirect."""
        return None
