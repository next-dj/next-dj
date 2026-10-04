"""`manage.py showmetadata`, naming the source of every metadata key of one page."""

from argparse import ArgumentParser
from pathlib import Path
from typing import Any, override

from django.core.management.base import BaseCommand, CommandError
from django.urls import Resolver404, resolve

from next.pages import page
from next.pages.errors import PageMetadataConflictError, PageMetadataShapeError
from next.pages.metadata.chain import metadata_origins


class Command(BaseCommand):
    """Show which segment of the chain sets each metadata key of a page."""

    help = "Show which settings tier or page.py sets every metadata key of a URL."

    @override
    def add_arguments(self, parser: ArgumentParser) -> None:
        parser.add_argument("path", help="The URL path of the page, like /blog/x/.")

    @override
    def handle(self, *args: Any, **options: Any) -> None:
        path = options["path"]
        try:
            match = resolve(path)
        except Resolver404 as exc:
            msg = f"{path} resolves to no URL"
            raise CommandError(msg) from exc
        page_path = getattr(match.func, "next_page_path", None)
        if not isinstance(page_path, Path):
            msg = f"{path} resolves to {match.view_name}, which is no page"
            raise CommandError(msg)
        try:
            chain = page.metadata_chain(page_path)
        except (PageMetadataShapeError, PageMetadataConflictError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(str(page_path))
        for origin in metadata_origins(chain):
            self.stdout.write(f"  {origin.key}: {origin.source}")
