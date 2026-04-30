from __future__ import annotations

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from next.urls import router_manager

from .models import Article


@receiver(post_save, sender=Article)
@receiver(post_delete, sender=Article)
def reload_router_on_article_change(**_kwargs: object) -> None:
    """Rebuild URL patterns whenever an article appears or disappears."""
    router_manager.reload()
