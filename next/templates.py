import gc
import hashlib
import inspect
from pathlib import Path
from typing import Any

from django.template import Template


class DJXCompiler:
    """DJXCompiler collects Django template blocks from code and stores them for later processing."""

    def __init__(self) -> None:
        """Initialize with empty caches and setup automatic memory cleanup."""
        self._templates: dict[str, Template | None] = {}
        self._content_hashes: dict[str, str] = {}
        self._file_paths: dict[str, str] = {}  # hash -> file path mapping
        self._file_blocks: dict[str, list[str]] = {}  # file path -> list of hashes

        # setup automatic cache cleanup on garbage collection
        self._setup_gc_callback()

    def __len__(self) -> int:
        """Number of collected template blocks."""
        return len(self._templates)

    def __contains__(self, template: str) -> bool:
        """Check if template exists in cache."""
        content_hash = self._generate_content_hash(template)
        return content_hash in self._templates

    def __mod__(self, template: str) -> "DJXCompiler":
        """
        % operator for adding template blocks.

        Automatically detects the file where it was called and saves the template.
        """
        # get the path to the file where the % operator was called
        current_frame = inspect.currentframe()
        if current_frame and current_frame.f_back:
            file_path = current_frame.f_back.f_code.co_filename
        else:
            file_path = "unknown"

        # generate hash for template content
        content_hash = self._generate_content_hash(template)

        # if there's already the same block in the same file - overwrite it
        if (
            content_hash in self._file_paths
            and self._file_paths[content_hash] == file_path
        ):
            self._remove_from_cache(content_hash)

        # create Django Template object
        try:
            django_template: Template | None = Template(template)
        except Exception:
            # if template doesn't parse - save as None
            django_template = None

        # save all data
        self._content_hashes[content_hash] = template  # save original string
        self._templates[content_hash] = django_template  # save Django Template object
        self._file_paths[content_hash] = file_path

        # add to the list of blocks for the file
        if file_path not in self._file_blocks:
            self._file_blocks[file_path] = []
        self._file_blocks[file_path].append(content_hash)

        return self

    def _setup_gc_callback(self) -> None:
        """Setup callback for automatic cleanup of unused template blocks."""

        def gc_callback(phase: str, info: dict[str, Any]) -> None:
            if phase == "stop":
                self._clear_expired_cache()

        gc.callbacks.append(gc_callback)

    def _clear_expired_cache(self) -> None:
        """Clear cache from template blocks that are no longer used."""
        expired_hashes = []

        for content_hash in list(self._content_hashes.keys()):
            # check if this block is still being used
            if not self._is_content_referenced(content_hash):
                expired_hashes.append(content_hash)

        for content_hash in expired_hashes:
            self._remove_from_cache(content_hash)

    def _is_content_referenced(self, content_hash: str) -> bool:
        """Check if there are still references to this template block."""
        return content_hash in self._content_hashes

    def _remove_from_cache(self, content_hash: str) -> None:
        """Remove template block from all caches."""
        if content_hash in self._templates:
            del self._templates[content_hash]

        if content_hash in self._content_hashes:
            del self._content_hashes[content_hash]

        if content_hash in self._file_paths:
            file_path = self._file_paths[content_hash]
            if file_path in self._file_blocks:
                if content_hash in self._file_blocks[file_path]:
                    self._file_blocks[file_path].remove(content_hash)
                if not self._file_blocks[file_path]:
                    del self._file_blocks[file_path]
            del self._file_paths[content_hash]

    def get_nodes(self, file_path: str | Path) -> list[Template]:
        """Get all Django Template objects from the specified file."""
        file_path_str = str(file_path)

        if file_path_str not in self._file_blocks:
            return []

        templates = []
        for content_hash in self._file_blocks[file_path_str]:
            template = self._templates.get(content_hash)
            if template is not None:
                templates.append(template)

        return templates

    def _generate_content_hash(self, content: str) -> str:
        """Generate SHA-256 hash for template content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


# global instance for use throughout the project
djx = DJXCompiler()
