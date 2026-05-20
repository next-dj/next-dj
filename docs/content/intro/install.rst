.. _intro-install:

Installation
============

This page installs next.dj into a fresh Django project, configures the page backend, and verifies that the file router is reachable.
By the end you have a project that renders a single page from the filesystem and is ready for :doc:`tutorial01`.

Requirements
------------

- Python 3.12 or newer (3.12, 3.13, 3.14 tested).
- Django 4.2 or newer (4.2, 5.0, 5.1, 5.2, 6.0 supported).
- An ASGI or WSGI server compatible with the Django version in use.

next.dj extends Django. It does not replace the ORM, migrations, admin, or auth (:ref:`intro-overview-django-unchanged`).

Install the Package
-------------------

Install the project package from PyPI.

.. code-block:: bash
   :caption: shell

   uv add next.dj
   # or
   pip install next.dj

Some installers normalise dots to hyphens in wheel and cache paths.
The import path is always ``next``.

Create a Django Project
-----------------------

If you do not already have a Django project, scaffold one in an empty folder.

.. code-block:: bash
   :caption: shell

   uv run django-admin startproject config .
   uv run python manage.py startapp notes

The tutorial uses ``config`` as the project package and ``notes`` as the first application.
The same instructions work with any other names if you adapt the imports.

Add next.dj to INSTALLED_APPS
-----------------------------

Open ``config/settings.py`` and register both ``next`` and your application in :doc:`INSTALLED_APPS <django:ref/applications>`.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django.contrib.sessions",
       "django.contrib.messages",
       "django.contrib.staticfiles",
       "next",
       "notes",
   ]

This list replaces the one ``django-admin startproject`` generates.
It intentionally drops ``django.contrib.admin`` because next.dj does not require the admin site.
Add ``django.contrib.admin`` back if the project needs it.

The ``next`` app registers system checks, template tags, autoreload hooks, and signal connections at startup.

Configure NEXT_FRAMEWORK
------------------------

Tell next.dj where to look for pages and components.
With ``APP_DIRS`` set to ``True`` and ``DIRS`` left empty, each installed app carries its own ``pages/`` and ``_components/`` directories.
The file router walk registers each ``_components/`` folder it encounters during page discovery, so the component backend needs no separate ``APP_DIRS`` flag.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           }
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

``PAGES_DIR`` is set to ``pages``, the built-in default, so next.dj scans a ``pages/`` directory inside each app.
``_components`` is the per-application folder the component backend scans, covered in :doc:`tutorial03`.
A ``FileRouterBackend`` entry must carry an ``OPTIONS`` key, and ``manage.py check`` reports ``next.E026`` if it is missing.

Keep ``django.template.context_processors.request`` in the ``OPTIONS`` of your ``TEMPLATES`` setting.
A fresh ``django-admin startproject`` already includes it, and ``manage.py check`` reports ``next.E019`` if it is missing.

Mount the Router
----------------

.. note::

   Without this ``include("next.urls")`` edit Django never reaches the file router.
   Every page returns a 404 until the line is in place.

Forward all unmatched URLs to next.dj by replacing ``config/urls.py`` with the file below.
This replacement also removes the ``admin`` import that ``startproject`` generated, which pairs with dropping ``django.contrib.admin`` from ``INSTALLED_APPS`` above.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("", include("next.urls")),
   ]

URLs declared above the ``include`` keep working.
Anything not matched by Django falls through to the file router, which resolves it against your ``pages/`` tree.

Create Your First Page
----------------------

Create one page in the ``notes`` application to confirm the wiring.

.. code-block:: python
   :caption: notes/pages/page.py

   from next.pages import context

   @context("title")
   def page_title() -> str:
       return "Notes"

.. code-block:: jinja
   :caption: notes/pages/template.djx

   <!doctype html>
   <html>
     <body>
       <h1>{{ title }}</h1>
     </body>
   </html>

The directory ``notes/pages/`` is the page root for the application.
The ``page.py`` plus ``template.djx`` pair turns the empty path into a rendered URL.

Run the Server
--------------

Apply Django migrations and start the development server.
A fresh ``startproject`` configures SQLite by default, so ``migrate`` creates the ``db.sqlite3`` file in the project root.

.. code-block:: bash
   :caption: shell

   uv run python manage.py migrate
   uv run python manage.py runserver

Open ``http://127.0.0.1:8000/`` and you should see the ``Notes`` heading.

Verify the Install
------------------

Run the Django system checks once to confirm the configuration matches the framework expectations.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A clean check run prints ``System check identified no issues`` and exits with status zero.
If a check fires, the message includes both the configuration key and the recommended fix.

Next Steps
----------

The environment is ready for the tutorial.

.. seealso::

   :doc:`tutorial01` builds the first real page of the Notes application.
   :doc:`/content/topics/project-layout` explains where files belong as the project grows.
   :doc:`/content/deployment/index` covers production setup once the application is feature complete.
