from typing import override

from django.contrib import admin
from django.forms import ModelForm
from django.http import HttpRequest
from django.utils import timezone

from next.partial import Patches
from next.realtime import publish
from polls.models import Poll


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
        deadline_ms = (
            int(obj.closes_at.timestamp() * 1000) if obj.closes_at is not None else None
        )
        topic = f"poll:{obj.pk}"
        payload = {
            "is_open": obj.is_open,
            "accepting_votes": obj.accepts_votes(at=timezone.now()),
            "deadline": deadline_ms,
        }
        using = obj._state.db or "default"
        publish(
            topic,
            Patches(None).event("poll:phase", payload),
            on_commit=True,
            using=using,
        )
        publish(
            topic,
            Patches(None).refresh(zone="poll-results"),
            on_commit=True,
            using=using,
        )
