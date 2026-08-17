.. _howto-write-a-router-backend:

Write a router backend
======================

Problem
-------

The file router covers pages that live on disk.
Some URLs live in a database table instead, and you want them served from the same router.

Solution
--------

Subclass ``FileRouterBackend`` and override ``generate_urls``.
Call ``super().generate_urls()`` for the file routes, then append one ``URLPattern`` per database row.
Register the subclass through ``PAGE_BACKENDS`` and reload it from a signal receiver when the table changes.

Walkthrough
-----------

Subclass FileRouterBackend
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``RouterBackend`` is the abstract base.
Its one abstract method is ``generate_urls``, which returns the patterns the backend contributes to the project URLconf.
It also carries ``page_roots``, which reports the page trees the system checks walk as ``PageRoot`` entries, each pairing a directory with the label the reports name it by.
Implement ``page_roots`` when the backend has page trees.
The base returns an empty list, so a backend that does not implement it is not checked and not watched, which is the right answer for a backend routing from a database or another source that is no tree on disk.
A subclass of ``FileRouterBackend`` inherits the file router's own implementation and is checked as it stands.
Report only trees the backend actually serves from, because the same list is what the development watcher observes, and a tree missing from it is neither checked nor reloaded.
``components_folder_name`` completes that pair by naming the folder the backend registers components from while it walks, ``None`` for a backend that registers none.
``skip_dir_names`` names the directories the backend's own walk refuses to enter, an empty set for a backend that refuses none.

The checks walk the reported trees themselves rather than asking the backend to walk them, so ``page_roots`` is the whole contract for being checked.
A backend that reports a tree is reached by every page check, from the structural ones to ``next.E017`` and the partial zone checks.
The names that walk refuses to enter are the folder ``components_folder_name`` returns plus the names ``skip_dir_names`` returns.
Both come from the backend under check rather than from the settings, so one entry's ``DIRS`` never blinds the checks to another entry's trees.
``FileRouterBackend`` answers with the skip set it built from its own ``DIRS``, the entries that name no existing directory.

``page_roots`` and ``components_folder_name`` are read on the ``runserver``, ``collectstatic``, and staticfiles paths.
An override that reaches a database or a network is therefore called where an outage must not stop the process.
The framework guards every such read, and it guards the value as well as the call.
A backend that raises, or that answers something other than ``PageRoot`` entries, contributes no tree for that call instead of reaching a caller that dereferences it, and one that names its components folder as anything but a string contributes no component glob.
``skip_dir_names`` is guarded the same way, and a backend that raises there or answers anything but a collection of names refuses no directory to the check walk.
The first such failure is logged once and the repeats stay quiet, because these paths run per reloader tick and per static lookup, and a backend that raised is logged with its traceback while one that answered the wrong type is named by that type.
``manage.py check`` reports the same failure once as ``next.E030``, with the traceback, which is where a human is reading output on purpose.
An override that can fail slowly is still worth a cache of its own, because nothing upstream caches the answer for it.
``FileRouterBackend`` already implements filesystem discovery, so subclass it and extend ``generate_urls`` rather than starting from the bare base.

.. code-block:: python
   :caption: wiki/backends.py

   from collections.abc import Callable
   from django.apps import apps as django_apps
   from django.db.utils import DatabaseError
   from django.urls import URLPattern, URLResolver, path
   from next.conf import next_framework_settings
   from next.urls import FileRouterBackend

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

Reuse the file route view
~~~~~~~~~~~~~~~~~~~~~~~~~~

The database URLs do not need their own view.
A file route at ``wiki/routes/wiki/[slug]/page.py`` already renders an article from a captured ``slug``.
Locate that catchall :doc:`URLPattern <django:ref/urls>` by its reverse name and reuse its callback.

.. code-block:: python
   :caption: wiki/backends.py

       def _find_catchall(
           self, urls: list[URLPattern | URLResolver]
       ) -> URLPattern | None:
           """Locate the file pattern that handles every article slug."""
           target = next_framework_settings.URL_NAME_TEMPLATE.format(name="wiki_slug")
           for url in urls:
               if isinstance(url, URLPattern) and getattr(url, "name", None) == target:
                   return url
           return None

The reverse name follows ``URL_NAME_TEMPLATE``, which defaults to ``page_{name}``.
A dynamic segment named ``[slug]`` yields the route name ``wiki_slug``.
A typed segment such as ``[int:slug]`` would instead yield ``wiki_int_slug`` because the converter token survives into the name.

Append one pattern per row
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Read the slugs from the model and build a :func:`django.urls.path` for each.
Every alias points at the shared callback and binds a fixed ``slug`` keyword, so the catchall view receives the right URL parameter.
Catch ``django.db.utils.DatabaseError`` because the backend can run before migrations have created the table.

.. code-block:: python
   :caption: wiki/backends.py

       def _build_article_aliases(
           self, view: Callable[..., object]
       ) -> list[URLPattern]:
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

Register the backend
~~~~~~~~~~~~~~~~~~~~~

List the dotted path of the subclass under ``PAGE_BACKENDS``.
``RouterFactory`` imports the class and instantiates it with the same ``PAGES_DIR``, ``APP_DIRS``, ``DIRS``, and ``OPTIONS`` keys a plain ``FileRouterBackend`` accepts.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
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

Register a backend that is not a file router
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A backend that subclasses ``RouterBackend`` directly rather than ``FileRouterBackend`` takes a shorter entry.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {"BACKEND": "wiki.backends.DatabaseRouterBackend"},
       ],
   }

``RouterFactory`` calls the class with no arguments, so a backend that is not a file router reads its own configuration.
The entry carries ``BACKEND`` and nothing else, and any other key reports ``next.E035``.

Reload when the table changes
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``generate_urls`` runs once per router build, so a new row stays invisible until the router rebuilds.
Connect a receiver to ``post_save`` and ``post_delete`` and call ``router_manager.reload()``.

.. code-block:: python
   :caption: wiki/receivers.py

   from django.db.models.signals import post_delete, post_save
   from django.dispatch import receiver
   from next.urls import router_manager
   from .models import Article

   @receiver(post_save, sender=Article)
   @receiver(post_delete, sender=Article)
   def reload_router_on_article_change(**kwargs) -> None:
       """Rebuild URL patterns whenever an article appears or disappears."""
       router_manager.reload()

The reload drops the cached backends, rebuilds them from ``PAGE_BACKENDS``, clears the Django URL resolver caches, and emits ``router_reloaded``.

Verification
------------

Add a row to the underlying table.
The next request resolves ``/wiki/<slug>/`` and ``reverse("next:wiki_article_<slug>")`` succeeds without a server restart.

Run ``uv run python manage.py check`` and confirm the backend is registered.

See also
--------

.. seealso::

   :doc:`/content/howto/reload-routes-from-code` for the reload trigger on its own.
   :doc:`/content/topics/file-router` for the file discovery the subclass extends.
   :doc:`/content/internals/url-router` for the manager and factory internals.
