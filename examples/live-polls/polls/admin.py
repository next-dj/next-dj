from typing import override

from django.contrib import admin
from django.forms import ModelForm
from django.http import HttpRequest

from next.partial import Patches
from next.realtime import publish
from polls.models import Poll
from polls.realtime import publish_phase


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    """Give staff a Django-native host console for opening and closing polls."""

    list_display = ("question", "is_open", "closes_at", "created_at")
    list_filter = ("is_open",)
    search_fields = ("question",)
    readonly_fields = ("created_at",)

    @override
    def save_model(
        self, request: HttpRequest, obj: Poll, form: ModelForm, change: bool
    ) -> None:
        """Notify open poll pages after host state is committed."""
        super().save_model(request, obj, form, change)
        publish_phase(obj)
        using = obj._state.db or "default"
        publish(
            f"poll:{obj.pk}",
            Patches(None).refresh(zone="poll-results"),
            on_commit=True,
            using=using,
        )
