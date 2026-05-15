.. _intro-install:

Installation
============

This page installs next.dj into a fresh Django project, configures the page backend, and verifies that the file router is reachable.
By the end you have a project that renders a single page from the filesystem and is ready for :doc:`tutorial01`.

Requirements
------------

next.dj depends on Python 3.12 or newer and Django 4.2 or newer.

- Python 3.12, 3.13, or 3.14.
- Django 4.2, 5.0, 5.1, 5.2, or 6.0.
- An ASGI or WSGI server compatible with your chosen Django version.

Install the Package
-------------------

next.dj is published on PyPI under the distribution name ``next-dj``.
Inside a project virtualenv, install it through your usual package manager.

.. code-block:: bash
   :caption: shell

   uv pip install next-dj
   # or
   pip install next-dj

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

Open ``config/settings.py`` and register both ``next`` and your application.

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

The ``next`` app registers system checks, template tags, autoreload hooks, and signal connections at startup.
Place it before your application so that those hooks run first.

Configure NEXT_FRAMEWORK
------------------------

Tell next.dj where to look for pages and components.
The values below treat each Django application as a self-contained unit.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "routes",
           }
       ],
       "DEFAULT_COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

Mount the Router
----------------

Forward all unmatched URLs to next.dj by adding one line to ``config/urls.py``.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("", include("next.urls")),
   ]

URLs declared above the ``include`` keep working.
Anything not matched by Django falls through to the file router, which resolves it against your ``routes/`` tree.

Create Your First Page
----------------------

Create one page in the ``notes`` application to confirm the wiring.

.. code-block:: python
   :caption: notes/routes/page.py

   from next.pages import context


   @context("title")
   def page_title() -> str:
       return "Notes"

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <!doctype html>
   <html>
     <body>
       <h1>{{ title }}</h1>
     </body>
   </html>

The directory ``notes/routes/`` is the page root for the application.
The ``page.py`` plus ``template.djx`` pair turns the empty path into a rendered URL.

Run the Server
--------------

Apply Django migrations and start the development server.

.. code-block:: bash
   :caption: shell

   uv run python manage.py migrate
   uv run python manage.py runserver

Open ``http://127.0.0.1:8000/`` and you should see the ``Notes`` heading.

Verify the Install
------------------

next.dj contributes several Django system checks.
Run them once to confirm the configuration matches the framework expectations.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A clean check run prints ``System check identified no issues`` and exits with status zero.
If a check fires, the message includes both the configuration key and the recommended fix.

.. note::

   The first time you import ``next``, the framework records the current ``NEXT_FRAMEWORK`` snapshot.
   Subsequent settings reloads emit a ``settings_reloaded`` signal so that long-lived processes can refresh caches.

Next Steps
----------

The environment is ready for the tutorial.

.. seealso::

   :doc:`tutorial01` builds the first real page of the Notes application.
   :doc:`/content/topics/project-layout` explains where files belong as the project grows.
   :doc:`/content/deployment/index` covers production setup once the application is feature complete.
