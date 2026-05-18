.. _howto-build-a-custom-asset-backend:

Resolve Asset URLs Through a Custom Backend
===========================================

Pick this page when the asset URL must come from an external source such as a Vite manifest.
To change only the tag markup or attributes, see :doc:`/content/howto/write-a-static-backend`.

Problem
-------

You registered a ``.jsx`` kind, but the asset URL must be resolved through a Vite manifest instead of the Django staticfiles namespace.
The bundled backend cannot do that lookup.

Solution
--------

Subclass ``StaticFilesBackend``.
Override ``register_file`` to resolve the URL your own way and delegate every other kind to the parent.
Register the kind through ``next.static.default_kinds`` against a bundled renderer and point ``DEFAULT_STATIC_BACKENDS`` at the subclass.

Walkthrough
-----------

Resolve URLs in ``register_file``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``register_file`` is the only abstract method on the backend contract.
The default implementation maps a logical name onto the Django staticfiles namespace.
A custom backend can intercept one kind and resolve it elsewhere, then delegate every other kind to the parent.

.. code-block:: python
   :caption: kanban/backends.py

   from __future__ import annotations

   import json
   import logging
   from pathlib import Path
   from typing import TYPE_CHECKING, Any

   from django.contrib.staticfiles.storage import staticfiles_storage

   from next.static import StaticFilesBackend


   if TYPE_CHECKING:
       from collections.abc import Mapping


   logger = logging.getLogger(__name__)


   class ViteManifestBackend(StaticFilesBackend):
       def __init__(self, config: Mapping[str, Any] | None = None) -> None:
           super().__init__(config)
           opts = dict(self._config.get("OPTIONS") or {})
           self._dev_origin: str = opts.get("DEV_ORIGIN", "")
           self._vite_root: str = opts.get("VITE_ROOT", "")
           self._manifest_path: str = opts.get("MANIFEST_PATH", "")
           self._manifest_data: dict[str, Any] | None = None
           self._manifest_missing_warned = False

       def register_file(
           self,
           source_path: Path,
           logical_name: str,
           kind: str,
       ) -> str:
           if kind != "jsx":
               return super().register_file(source_path, logical_name, kind)
           if self._dev_origin:
               return self._build_dev_url(source_path)
           if self._manifest_path:
               return self._resolve_from_manifest(source_path, logical_name)
           return super().register_file(source_path, logical_name, kind)

The constructor reads its own keys from the ``OPTIONS`` mapping.
``register_file`` receives the absolute ``source_path``, the extension-free ``logical_name``, and the registered ``kind``.
Every kind except ``jsx`` falls straight through to ``super().register_file``.

Read the Vite manifest
~~~~~~~~~~~~~~~~~~~~~~

A production build writes hashed filenames into ``dist/.vite/manifest.json``.
The backend reads that file once, caches it, and looks up the built output.
A missing manifest logs one warning and falls back to staticfiles so the dev workflow stays unblocked.

.. code-block:: python
   :caption: kanban/backends.py

   def _resolve_from_manifest(self, source_path: Path, logical_name: str) -> str:
       manifest = self._load_manifest()
       if manifest is None:
           return super().register_file(source_path, logical_name, "jsx")
       key = self._manifest_key(source_path)
       entry = manifest.get(key)
       if entry:
           built = entry["file"]
           return str(staticfiles_storage.url(f"kanban/dist/{built}"))
       return super().register_file(source_path, logical_name, "jsx")

   def _load_manifest(self) -> dict[str, Any] | None:
       if self._manifest_data is not None:
           return self._manifest_data
       path = Path(self._manifest_path)
       if not path.exists():
           if not self._manifest_missing_warned:
               logger.warning(
                   "Vite manifest not found at %s. Falling back to staticfiles. "
                   "Run `npm run build` to generate it.",
                   path,
               )
               self._manifest_missing_warned = True
           return None
       with path.open() as f:
           self._manifest_data = json.load(f)
       return self._manifest_data

URL resolution delegates to ``staticfiles_storage`` so manifest storage, S3 storage, and CDN settings still apply to the hashed output.

Register the Kind
~~~~~~~~~~~~~~~~~

Register the ``jsx`` kind in ``AppConfig.ready``.
The ``renderer`` points at the bundled ``render_module_tag`` method, so the resolved ``.jsx`` URL renders as a module script.
Register the ``page`` stem too so discovery picks up ``page.jsx`` alongside ``page.css``.

.. code-block:: python
   :caption: kanban/apps.py

   from django.apps import AppConfig

   from next.static import default_kinds
   from next.static.discovery import default_stems


   class KanbanConfig(AppConfig):
       default_auto_field = "django.db.models.BigAutoField"
       name = "kanban"

       def ready(self) -> None:
           default_kinds.register(
               "jsx",
               extension=".jsx",
               slot="scripts",
               renderer="render_module_tag",
           )
           default_stems.register("template", "page")

See :doc:`/content/topics/static-assets/asset-kinds` for the ``register`` signature.
The ``scripts`` slot means ``{% collect_scripts %}`` in the layout emits the tag.
The manager looks the renderer up on the active backend with ``getattr`` per asset.
A subclass that needs a tag shape the bundled methods do not produce can add its own renderer method and name it here.

Register the Backend
~~~~~~~~~~~~~~~~~~~~

List the subclass in ``DEFAULT_STATIC_BACKENDS``.
Every key under ``OPTIONS`` reaches the constructor through ``self._config``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_STATIC_BACKENDS": [
           {
               "BACKEND": "kanban.backends.ViteManifestBackend",
               "OPTIONS": {
                   "DEV_ORIGIN": "http://localhost:5173",
                   "VITE_ROOT": str(BASE_DIR),
                   "MANIFEST_PATH": str(
                       BASE_DIR / "kanban/static/kanban/dist/.vite/manifest.json"
                   ),
               },
           },
       ],
   }

Ship the Asset
~~~~~~~~~~~~~~

Drop a ``component.jsx`` file next to the ``component.py`` it belongs to.
Discovery finds it because ``component`` is a registered stem and ``.jsx`` is now a registered extension.

.. code-block:: jinja
   :caption: kanban/boards/board/[int:id]/_pieces/card/component.jsx

   import { useState } from "react";

   export function Card({ id, title, excerpt }) {
     const [dragging, setDragging] = useState(false);
     return <div data-kanban-card={id}>{title}</div>;
   }

Verification
------------

Run ``uv run python manage.py check`` and confirm no warnings.

Load a board page and inspect the HTML source.
A ``<script type="module">`` tag points at the ``.jsx`` asset.
With a Vite build present, the ``src`` is the hashed file from ``dist/.vite/manifest.json``.
Delete the manifest and reload.
The page still renders and the log carries a single fallback warning.

See Also
--------

.. seealso::

   :doc:`/content/howto/add-a-new-asset-kind` for registering a kind against a bundled renderer.
   :doc:`/content/howto/write-a-static-backend` for attribute only and URL rewriting backends.
   :doc:`/content/topics/static-assets/index` for the static pipeline overview.
