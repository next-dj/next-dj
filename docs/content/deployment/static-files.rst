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

next.dj contributes a staticfiles finder that exposes every co-located asset to Django's standard pipeline.
Production deployments therefore use ``collectstatic`` exactly as they would for any other Django project.

Build Step
----------

Run ``collectstatic`` during the deployment build.

.. code-block:: bash
   :caption: shell

   uv run python manage.py collectstatic --noinput

Django copies every co-located ``component.css``, ``component.js``, ``component.mjs``, ``layout.css``, ``template.css``, and any custom stem file into ``STATIC_ROOT``.
Project ``static/`` directories and any directory listed in ``STATICFILES_DIRS`` are copied as well.

Hashed URLs
-----------

The default ``StaticBackend`` appends a content hash to the URL of every asset.
The hash changes only when the file content changes, which makes long lived browser cache lifetimes safe.

.. code-block:: text
   :caption: rendered output example

   <link rel="stylesheet" href="/static/components/note_card/component.css?h=a1b2c3d4">

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

   from next.static.backends import StaticBackend
   from next.static.assets import Asset


   CDN = "https://cdn.example.com"


   class CdnBackend(StaticBackend):
       def url(self, asset: Asset) -> str:
           return f"{CDN}{asset.relative_path}?h={asset.content_hash[:8]}"

Register the backend in ``DEFAULT_STATIC_BACKENDS`` and configure the CDN to pull from the static origin.

Pre Compressed Files
--------------------

For Brotli or gzip support, generate the compressed files during the build.

.. code-block:: bash
   :caption: shell

   uv run python manage.py collectstatic --noinput
   find STATIC_ROOT -type f \( -name "*.css" -o -name "*.js" \) -exec brotli -f -k {} \;

Configure the web server or CDN to serve the pre compressed copies based on the ``Accept-Encoding`` header.

Service Workers
---------------

A service worker that caches assets must invalidate when the content hash changes.
Read the rendered URL and key the cache on the full path including the hash query string.

System Checks
-------------

Run ``uv run python manage.py check --deploy`` before shipping.
The framework deployment check verifies that ``STATIC_ROOT`` is set, ``STATIC_URL`` is reachable, and the static backend chain is valid.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/howto/write-a-static-backend` for the backend recipe.
