.. _howto-write-a-router-backend:

Write a Router Backend
======================

Problem
-------

The file router covers pages that live on disk.
Some URLs live in a database table instead, and you want them served from the same router.

Solution
--------

Subclass ``FileRouterBackend`` and override ``generate_urls``.
Call ``super().generate_urls()`` for the file routes, then append one ``URLPattern`` per database row.
Register the subclass through ``DEFAULT_PAGE_BACKENDS`` and reload it from a signal receiver when the table changes.

Walkthrough
-----------

Subclass FileRouterBackend
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``RouterBackend`` is the abstract base.
Its only contract is ``generate_urls``, which returns the patterns the backend contributes to the project URLconf.
``FileRouterBackend`` already implements filesystem discovery, so subclass it and extend ``generate_urls`` rather than starting from the bare base.

.. code-block:: python
   :caption: wiki/backends.py

   from __future__ import annotations
   from typing import TYPE_CHECKING
   from django.apps import apps as django_apps
   from django.db.utils import DatabaseError
   from django.urls import URLPattern, path
   from next.conf import next_framework_settings
   from next.urls import FileRouterBackend

   if TYPE_CHECKING:
       from collections.abc import Callable

       from django.urls import URLResolver

   PUBLIC_PREFIX = "wiki"

   class HybridRouterBackend(FileRouterBackend):
       """File router that also publishes one named URL per Article row."""

       def generate_urls(self) -> list[URLPattern | URLResolver]:
           """Return file routes plus a named alias per article."""
           urls = list(super().generate_urls())
           catchall = self._find_catchall(urls)
           if catchall is None:
               return urls
           urls.extend(self._build_article_aliases(catchall.callback))
           return urls

The call to ``super().generate_urls()`` keeps every file route intact.
The subclass only adds patterns.

Reuse the File Route View
~~~~~~~~~~~~~~~~~~~~~~~~~~

The database URLs do not need their own view.
A file route at ``wiki/routes/wiki/[slug]/page.py`` already renders an article from a captured ``slug``.
Locate that catchall :doc:`URLPattern <django:ref/urls>` by its reverse name and reuse its callback.

.. code-block:: python
   :caption: wiki/backends.py

   def _find_catchall(self, urls: list[URLPattern | URLResolver]) -> URLPattern | None:
       """Locate the file pattern that handles every article slug."""
       target = next_framework_settings.URL_NAME_TEMPLATE.format(name="wiki_slug")
       for url in urls:
           if isinstance(url, URLPattern) and getattr(url, "name", None) == target:
               return url
       return None

The reverse name follows ``URL_NAME_TEMPLATE``, which defaults to ``page_{name}``.
A dynamic segment named ``[slug]`` yields the route name ``wiki_slug``.

Append One Pattern Per Row
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Read the slugs from the model and build a :func:`django.urls.path` for each.
Every alias points at the shared callback and binds a fixed ``slug`` keyword, so the catchall view receives the right URL parameter.
Catch ``django.db.utils.DatabaseError`` because the backend can run before migrations have created the table.

.. code-block:: python
   :caption: wiki/backends.py

   def _build_article_aliases(self, view: Callable[..., object]) -> list[URLPattern]:
       """Materialise one named URL per existing article slug."""
       article_model = django_apps.get_model("wiki", "Article")
       try:
           slugs = list(article_model.objects.values_list("slug", flat=True))
       except DatabaseError:
           return []
       return [
           path(
               f"{PUBLIC_PREFIX}/{slug}/",
               view,
               kwargs={"slug": slug},
               name=f"wiki_article_{slug}",
           )
           for slug in slugs
       ]

Each alias gets a unique reverse name of ``wiki_article_<slug>`` so templates can call :func:`~django.urls.reverse` per article.
Patterns mounted through ``include("next.urls")`` carry the ``next`` application namespace, so the lookup is ``reverse("next:wiki_article_<slug>")``.
Names a custom backend registers land in the same ``next`` namespace.

Register the Backend
~~~~~~~~~~~~~~~~~~~~~

List the dotted path of the subclass under ``DEFAULT_PAGE_BACKENDS``.
``RouterFactory`` imports the class and instantiates it with the same ``PAGES_DIR``, ``APP_DIRS``, ``DIRS``, and ``OPTIONS`` keys a plain ``FileRouterBackend`` accepts.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "wiki.backends.HybridRouterBackend",
               "APP_DIRS": True,
               "DIRS": [str(BASE_DIR / "shell")],
               "PAGES_DIR": "routes",
               "OPTIONS": {
                   "context_processors": [
                       "django.template.context_processors.request",
                   ],
               },
           },
       ],
   }

Reload When the Table Changes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``generate_urls`` runs once per router build, so a new row stays invisible until the router rebuilds.
Connect a receiver to ``post_save`` and ``post_delete`` and call ``router_manager.reload()``.

.. code-block:: python
   :caption: wiki/receivers.py

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

The reload drops the cached backends, rebuilds them from ``DEFAULT_PAGE_BACKENDS``, clears the Django URL resolver caches, and emits ``router_reloaded``.

Verification
------------

Add a row to the underlying table.
The next request resolves ``/wiki/<slug>/`` and ``reverse("next:wiki_article_<slug>")`` succeeds without a server restart.

Run ``uv run python manage.py check`` and confirm the backend is registered.

See Also
--------

.. seealso::

   :doc:`/content/howto/reload-routes-from-code` for the reload trigger on its own.
   :doc:`/content/topics/file-router` for the file discovery the subclass extends.
   :doc:`/content/internals/url-router` for the manager and factory internals.
