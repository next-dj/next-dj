.. _deployment-static-files:

Static files in production
==========================

This page covers how to serve next.dj static assets in production.
It covers the build step, the staticfiles finder integration, content hashing, asset versioning, and the CDN host.

.. contents::
   :local:
   :depth: 2

Overview
--------

next.dj contributes ``NextStaticFilesFinder``, exported as ``next.static.NextStaticFilesFinder``.
The finder exposes every co-located asset to Django's :doc:`standard staticfiles pipeline <django:howto/static-files/index>`.
``NextFrameworkConfig.ready`` appends it to ``STATICFILES_FINDERS`` automatically, so no manual configuration is required.
Production deployments use :doc:`collectstatic <django:ref/contrib/staticfiles>` exactly as they would for any other Django project.

Verify the finder
~~~~~~~~~~~~~~~~~

To confirm the finder is active, run the command below with a path that matches a component the project actually ships.

.. code-block:: bash
   :caption: shell

   uv run python manage.py findstatic next/components/note_card.css

If the file is not found, check that ``next`` is in ``INSTALLED_APPS`` and that the component named in the path exists.

Build step
----------

Run ``collectstatic`` during the deployment build.

.. code-block:: bash
   :caption: shell

   uv run python manage.py collectstatic --noinput

Django copies every co-located file into ``STATIC_ROOT``.
The default stems are ``component``, ``layout``, and ``template``, the default kinds are ``css``, ``js``, and ``module`` (extensions ``.css``, ``.js``, ``.mjs``).
Files registered under custom stems and custom kinds are copied through the same finder, so an extension added through ``default_kinds.register`` ships with the rest.
Project ``static/`` directories and any directory listed in ``STATICFILES_DIRS`` are copied as well.

The framework's ``next/static`` directory is the ``next.static`` Python package rather than an application static directory, and ``NextAppDirectoriesFinder`` keeps it out of the app-directories scan, so ``collectstatic`` copies no framework module and no bytecode cache.
A ``STATIC_ROOT`` holding Python sources at its top level is a leftover from a collect that ran under a finder which published them, and ``collectstatic --clear`` empties the directory before it collects the current set.
A project-written ``AppDirectoriesFinder`` subclass that reintroduces the problem is refused as ``next.E083``.

Hashed URLs
-----------

The default ``StaticFilesBackend`` resolves every asset URL through Django staticfiles.
Pair it with Django's :doc:`ManifestStaticFilesStorage <django:ref/contrib/staticfiles>` so each URL carries a content hash that changes only when the file content changes.
Stable hashes make long lived browser cache lifetimes safe.

.. code-block:: text
   :caption: rendered output example

   <link rel="stylesheet" href="/static/next/components/note_card.a1b2c3d4.css">

An asset named from a template or a module list carries the hash on the same terms, because a name is resolved through the same storage, see :doc:`/content/topics/static-assets/name-resolution`.
A hardcoded public path such as ``/static/site/app.css`` is the one spelling that skips it, so write the name and let staticfiles supply the URL.

Configure the web server or the CDN to honour long ``Cache-Control`` headers on the static origin.

Manifest storage
----------------

For projects that use Django ``ManifestStaticFilesStorage`` the framework cooperates without extra configuration.
``collectstatic`` writes the manifest, the framework reads it at runtime, and the rendered HTML uses the manifested filenames.

The shipped ``next.min.js`` carries no ``sourceMappingURL`` comment, so manifest post-processing finds no reference to rewrite and no map a wheel install leaves out.
``collectstatic`` under manifest storage therefore completes on a wheel install with no ignore pattern and no post-processing exclusion.

.. code-block:: python
   :caption: config/settings.py

   STORAGES = {
       "staticfiles": {
           "BACKEND": "django.contrib.staticfiles.storage.ManifestStaticFilesStorage",
       },
   }

Asset versioning
----------------

``ManifestStaticFilesStorage`` is the recommended way to make a browser revalidate after a deploy.
It addresses each file by its content, so only a file that changed gets a new URL while everything else stays in the client cache.

``NEXT_FRAMEWORK["STATIC_VERSION"]`` serves a project that cannot run the manifest.
It sets a ``v`` query parameter on every URL the pipeline renders, co-located files, named assets, ``{% asset %}`` values, and the ``next.min.js`` runtime alike.

.. code-block:: python
   :caption: config/settings.py

   import os

   NEXT_FRAMEWORK = {
       "STATIC_VERSION": os.environ["BUILD_ID"],
   }

The key defaults to ``None``, which leaves every URL untouched.
A global version invalidates every asset at once, including a vendor bundle nobody changed, so the two mechanisms are redundant together and a project on the manifest leaves the key unset.

Either mechanism also feeds the asset version a partial response stamps, so a deploy that moves the URLs moves the guard that asks an open tab to reload with them.
A project that runs neither stamps the same version on every deploy, and ``manage.py check --deploy`` reports that as ``next.W083``.

.. warning::

   The version value comes from outside the process, a build identifier or a commit hash, and is never generated at startup.
   Several workers would each invent their own, so a client would refetch one file once per worker, and a rolling deploy would hold that state for the whole window.

A single ``{% asset %}`` call overrides the project value with its own ``version`` argument, and ``version=""`` renders that one URL with no version at all.
A backend that returns a signed URL carries its own version, because a signature covers the query string and an appended parameter invalidates it.

CDN
---

A single CDN host in front of the static origin belongs in ``STATIC_URL``.

.. code-block:: python
   :caption: config/settings.py

   STATIC_URL = "https://cdn.example.com/static/"

Every path the project renders then agrees for free, a co-located asset, a named asset, and Django's own ``{% static %}`` alike, and no per-URL code runs.
Configure the CDN to pull from the static origin, and keep ``STATIC_ROOT`` and the ``collectstatic`` step unchanged.

Reserve the ``asset_url`` backend hook for a rewrite that genuinely varies per request, such as a per-tenant prefix.
A hook that prepends one constant host rewrites what the pipeline renders and nothing else, which splits a page between two hosts as soon as a template calls ``{% static %}`` or a third-party application renders an image.
See :ref:`Tenant URL prefix <howto-static-backend-tenant-prefix>` for the per-request case the hook exists for.

Pre compressed files
--------------------

For Brotli or gzip support, generate the compressed files during the build.

.. code-block:: bash
   :caption: shell

   uv run python manage.py collectstatic --noinput
   find ./staticfiles -type f \( -name "*.css" -o -name "*.js" \) -exec brotli -f -k {} \;

The ``./staticfiles`` path stands for the directory configured as ``STATIC_ROOT``.
Configure the web server or CDN to serve the pre compressed copies based on the ``Accept-Encoding`` header.

Service workers
---------------

A service worker that caches assets must invalidate when the content hash changes.
Read the rendered URL and key the cache on the full path, which already carries the content hash in the filename.

System checks
-------------

Run ``uv run python manage.py check --deploy`` before shipping.

The framework static checks validate the backend chain, the registered asset kinds, the inline asset bodies, the JS context serializer, and the finder wiring.
None of them is a deployment check, so they already run on every ``manage.py check`` and the build catches a malformed backend long before the deploy step.
What ``--deploy`` adds is Django's own hardening pass over ``STATIC_ROOT`` and ``STATIC_URL``, plus the three framework deployment checks that import or compile the whole page tree, see :doc:`checklist`.

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`/content/topics/static-assets/name-resolution` for the rule that decides a name from a URL.
   :doc:`/content/howto/use-a-compiled-stylesheet` for shipping a bundler output.
   :doc:`/content/howto/write-a-static-backend` for the backend recipe.
