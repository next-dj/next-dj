.. _howto-compiled-stylesheet:

Use a compiled stylesheet
=========================

Problem
-------

The design arrives as a compiled bundle from outside the project, a Tailwind, Sass, or bundler build, and it belongs to no page and no component.

Solution
--------

Write the build output into a directory staticfiles already reads, then name that file from the root ``layout.djx``.
Co-location answers the question "which template owns this file", and a compiled bundle owns none of them, so the named-asset spelling is the right one rather than a fallback.

Walkthrough
-----------

Point the build at a staticfiles directory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Django reads two kinds of source directory, an application ``static/`` directory and any directory listed in ``STATICFILES_DIRS``.
Either one works, and the choice is about who owns the bundle.

Write the output into the application that ships the design when one application owns it.

.. code-block:: text
   :caption: notes/static/site/

   app.css

Write it into a project-level directory when the bundle belongs to the site rather than to one application, and list that directory in ``STATICFILES_DIRS``.

.. code-block:: python
   :caption: config/settings.py

   STATICFILES_DIRS = [BASE_DIR / "build"]

.. code-block:: json
   :caption: package.json

   {
     "scripts": {
       "build:css": "tailwindcss -i ./styles/app.css -o ./build/site/app.css --minify",
       "watch:css": "tailwindcss -i ./styles/app.css -o ./build/site/app.css --watch"
     }
   }

The subdirectory matters, because the staticfiles name starts at the directory Django reads.
An output at ``build/site/app.css`` is named ``site/app.css``, and one written straight into ``build/`` would be named ``app.css`` and would collide with any other application shipping that filename.

Keep the source styles out of the collected tree.
A ``styles/`` directory beside ``build/`` is read by the bundler alone, so ``collectstatic`` never copies the uncompiled input.

Name the bundle from the root layout
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html>
     <head>
       {% use_style "site/app.css" %}
       {% collect_styles %}
     </head>
     <body>
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

The reference is a staticfiles name, so it resolves through ``STATIC_URL`` and through the configured storage, see :doc:`/content/topics/static-assets/name-resolution`.
The root layout wraps every page, so one tag covers the site.

For a raw attribute, such as a preload hint the collector has no slot for, ``{% asset %}`` returns the same URL as a string.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <link rel="preload" as="style" href="{% asset "site/app.css" %}">

Ship it to production
~~~~~~~~~~~~~~~~~~~~~

Run the bundler before ``collectstatic``, because ``collectstatic`` copies what is on disk at that moment.

.. code-block:: bash
   :caption: shell

   npm run build:css
   uv run python manage.py collectstatic --noinput

Under :doc:`ManifestStaticFilesStorage <django:ref/contrib/staticfiles>` the copied bundle is hashed like any other static file, and the rendered ``<link>`` carries that hash.
A bundle referenced as a hardcoded ``/static/site/app.css`` would skip the hash while every co-located asset on the same page carries one, which is the split the name closes.

Rebuild during development
~~~~~~~~~~~~~~~~~~~~~~~~~~

Framework reloading does not cover a compiled bundle.
Discovery re-probes co-located files under ``DEBUG`` and the Django development server restarts on a Python change, and neither of those runs the bundler.
Run the bundler in watch mode beside ``runserver``, so the file on disk is current and the browser picks it up on the next reload.

.. code-block:: bash
   :caption: shell

   npm run watch:css

The rendered URL does not change while ``DEBUG`` is on, because the development storage adds no content hash, so a browser holding the previous file may need a cache-bypassing reload.

.. note::

   The bundler owns the rebuild, and the framework owns the reference.
   Keep the compiled output out of version control and build it in the deployment pipeline, the same way the Python dependencies are installed there.

Verification
------------

Confirm staticfiles resolves the name.

.. code-block:: bash
   :caption: shell

   uv run python manage.py findstatic site/app.css

Load a page and read the ``<head>``.
One ``<link>`` points at the bundle, ahead of the co-located stylesheets, because tag assets are prepended.
After ``collectstatic`` with a hashed manifest, the same tag carries the hashed filename.

See also
--------

.. seealso::

   :doc:`ship-a-site-wide-stylesheet` for the three spellings of a site-wide stylesheet.
   :doc:`/content/topics/static-assets/name-resolution` for the rule that decides a name from a URL.
   :doc:`/content/deployment/static-files` for ``collectstatic`` and the manifest.
   :doc:`/content/howto/build-a-custom-asset-backend` for resolving URLs through a bundler manifest instead.
