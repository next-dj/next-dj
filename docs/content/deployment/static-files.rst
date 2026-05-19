.. _deployment-static-files:

Static Files in Production
==========================

This page covers how to serve next.dj static assets in production.
It covers the build step, the staticfiles finder integration, content hashing, and the CDN integration.

.. contents::
   :local:
   :depth: 2

Overview
--------

next.dj contributes ``NextStaticFilesFinder`` (dotted path ``next.static.NextStaticFilesFinder``) that exposes every co-located asset to Django's :doc:`standard staticfiles pipeline <django:howto/static-files/index>`.
``NextFrameworkConfig.ready`` appends it to ``STATICFILES_FINDERS`` automatically, so no manual configuration is required.
Production deployments use :doc:`collectstatic <django:ref/contrib/staticfiles>` exactly as they would for any other Django project.

Verify the Finder
~~~~~~~~~~~~~~~~~

To confirm the finder is active, run the command below with a path that matches a component the project actually ships.

.. code-block:: bash
   :caption: shell

   uv run python manage.py findstatic next/components/note_card.css

If the file is not found, check that ``next`` is in ``INSTALLED_APPS`` and that the component named in the path exists.

Build Step
----------

Run ``collectstatic`` during the deployment build.

.. code-block:: bash
   :caption: shell

   uv run python manage.py collectstatic --noinput

Django copies every co-located ``component.css``, ``component.js``, ``component.mjs``, ``layout.css``, ``layout.js``, ``layout.mjs``, ``template.css``, ``template.js``, ``template.mjs``, and any custom stem file into ``STATIC_ROOT``.
Project ``static/`` directories and any directory listed in ``STATICFILES_DIRS`` are copied as well.

Hashed URLs
-----------

The default ``StaticFilesBackend`` resolves every asset URL through Django staticfiles.
Pair it with Django's :doc:`ManifestStaticFilesStorage <django:ref/contrib/staticfiles>` so each URL carries a content hash that changes only when the file content changes, which makes long lived browser cache lifetimes safe.

.. code-block:: text
   :caption: rendered output example

   <link rel="stylesheet" href="/static/next/components/note_card.a1b2c3d4.css">

Configure the web server or the CDN to honour long ``Cache-Control`` headers on the static origin.

Manifest Storage
----------------

For projects that use Django ``ManifestStaticFilesStorage`` the framework cooperates without extra configuration.
``collectstatic`` writes the manifest, the framework reads it at runtime, and the rendered HTML uses the manifested filenames.

.. code-block:: python
   :caption: config/settings.py

   STORAGES = {
       "staticfiles": {
           "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
       },
   }

CDN
---

Use a CDN aware backend to point asset URLs at a CDN host.

.. code-block:: python
   :caption: notes/backends.py

   from next.static import StaticFilesBackend

   CDN = "https://cdn.example.com"

   class CdnBackend(StaticFilesBackend):
       def render_link_tag(self, url, *, request=None) -> str:
           return f'<link rel="stylesheet" href="{CDN}{url}">'

       def render_script_tag(self, url, *, request=None) -> str:
           return f'<script src="{CDN}{url}" defer></script>'

       def render_module_tag(self, url, *, request=None) -> str:
           return f'<script type="module" src="{CDN}{url}"></script>'

Register the backend in ``DEFAULT_STATIC_BACKENDS`` and configure the CDN to pull from the static origin.

Pre Compressed Files
--------------------

For Brotli or gzip support, generate the compressed files during the build.

.. code-block:: bash
   :caption: shell

   uv run python manage.py collectstatic --noinput
   find ./staticfiles -type f \( -name "*.css" -o -name "*.js" \) -exec brotli -f -k {} \;

The ``./staticfiles`` path stands for the directory configured as ``STATIC_ROOT``.
Configure the web server or CDN to serve the pre compressed copies based on the ``Accept-Encoding`` header.

Service Workers
---------------

A service worker that caches assets must invalidate when the content hash changes.
Read the rendered URL and key the cache on the full path, which already carries the content hash in the filename.

System Checks
-------------

Run ``uv run python manage.py check --deploy`` before shipping.
The Django deployment checks cover ``STATIC_ROOT`` and ``STATIC_URL``, and the framework static checks validate that the static backend chain is well formed.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/howto/write-a-static-backend` for the backend recipe.
