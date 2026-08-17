.. _intro-install:

Installation
============

This page installs next.dj, configures the page backend, and verifies that the file router is reachable.
It scaffolds a new Django project on the way, so a project that already exists starts at :ref:`intro-install-installed-apps` and keeps its own layout.
By the end you have a project that renders a single page from the filesystem and is ready for :doc:`tutorial01`.

Requirements
------------

- Python 3.12 or newer (3.12, 3.13, 3.14 tested).
- Django 5.2 or 6.0.
- Python 3.14 requires Django 6.0.
  Django 5.2 supports Python 3.12 and 3.13.
- An ASGI or WSGI server compatible with the Django version in use.

next.dj extends Django.
It does not replace the ORM, migrations, admin, or auth (:ref:`intro-overview-django-unchanged`).

Create the project folder
-------------------------

Both installers need an environment to install into.
``uv`` reads a ``pyproject.toml`` from the current directory, and ``pip`` installs into the active virtualenv.
Create the folder first, then initialise the environment inside it.

.. code-block:: bash
   :caption: shell, uv

   mkdir notes-site
   cd notes-site
   uv init --bare --python 3.12

.. code-block:: bash
   :caption: shell, pip

   mkdir notes-site
   cd notes-site
   python3.12 -m venv .venv
   source .venv/bin/activate

Any interpreter from the supported range works in place of ``python3.12``.
On Windows the activation command is ``.venv\Scripts\activate``.
Running ``uv add`` in a folder without a ``pyproject.toml`` fails with ``No pyproject.toml found in current directory or any parent directory``, which is what this step prevents.

Later commands on this page carry a ``uv run`` prefix.
Drop that prefix when an activated virtualenv already puts ``python`` and ``django-admin`` on the path.
A project that already has a manifest or an environment skips this section.

Install the package
-------------------

Install the project package from PyPI.

.. code-block:: bash
   :caption: shell

   uv add next.dj
   # or
   pip install next.dj

Some installers normalise dots to hyphens in wheel and cache paths.
The import path is always ``next``.

Create a Django project
-----------------------

If you do not already have a Django project, scaffold one in the folder created above.

.. code-block:: bash
   :caption: shell

   uv run django-admin startproject config .
   uv run python manage.py startapp notes

The tutorial uses ``config`` as the project package and ``notes`` as the first application.
The same instructions work with any other names if you adapt the imports.

.. _intro-install-installed-apps:

Add next.dj to INSTALLED_APPS
-----------------------------

Open ``config/settings.py`` and register both ``next`` and your application in :doc:`INSTALLED_APPS <django:ref/settings>`.

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

The list above is what the project scaffolded in the previous section needs.
A project that already exists keeps its own entries and adds a single ``next`` entry.
The list drops ``django.contrib.admin`` because next.dj does not require the admin site, so a project that uses the admin keeps that entry in place.

The ``next`` app registers system checks, template tag builtins, autoreload hooks, and static file collectors at startup.

Configure NEXT_FRAMEWORK
------------------------

Tell next.dj where to look for pages and components.
With ``APP_DIRS`` set to ``True`` and ``DIRS`` left empty, each installed app carries its own ``pages/`` tree, and a ``_components/`` folder placed inside that tree holds the app's components.
The file router walk registers each ``_components/`` folder it encounters while walking the page tree, so the component backend needs no separate ``APP_DIRS`` flag.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           }
       ],
       "COMPONENT_BACKENDS": [
           {
               "BACKEND": "next.components.FileComponentsBackend",
               "DIRS": [],
               "COMPONENTS_DIR": "_components",
           }
       ],
   }

``PAGES_DIR`` is set to ``pages``, the built-in default, so next.dj scans a ``pages/`` directory inside each app.
``_components`` is the per-application folder the component backend scans, placed inside the page tree and covered in :doc:`tutorial03`.
A ``FileRouterBackend`` entry must carry an ``OPTIONS`` key, and ``manage.py check`` reports ``next.E026`` if it is missing.

Keep ``django.template.context_processors.request`` in the ``OPTIONS`` of your ``TEMPLATES`` setting.
A fresh ``django-admin startproject`` already includes it, and ``manage.py check`` reports ``next.E019`` if it is missing.

Mount the router
----------------

Forward all unmatched URLs to next.dj by replacing ``config/urls.py`` with the file below.
This replacement also removes the ``admin`` import that ``startproject`` generated, which pairs with dropping ``django.contrib.admin`` from ``INSTALLED_APPS`` above.
A project that already has routes keeps its ``urlpatterns`` and appends the ``include`` line as the last entry.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("", include("next.urls")),
   ]

.. note::

   Without this ``include("next.urls")`` edit Django never reaches the file router.
   Every page returns a 404 until the line is in place.

URLs declared above the ``include`` keep working.
Anything not matched by Django falls through to the file router, which resolves it against your ``pages/`` tree.

Create your first page
----------------------

Create one page in the ``notes`` application to confirm the wiring.

.. code-block:: python
   :caption: notes/pages/page.py

   from next import context

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

Run the server
--------------

Apply Django migrations and start the development server.
A fresh ``startproject`` configures SQLite by default, so ``migrate`` creates the ``db.sqlite3`` file in the project root.

.. code-block:: bash
   :caption: shell

   uv run python manage.py migrate
   uv run python manage.py runserver

Open ``http://127.0.0.1:8000/`` and you should see the ``Notes`` heading.

Verify the install
------------------

Run the Django system checks once to confirm the configuration matches the framework expectations.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A clean check run prints ``System check identified no issues`` and exits with status zero.
If a check fires, the message includes both the configuration key and the recommended fix.

Next steps
----------

The environment is ready for the tutorial.

.. seealso::

   :doc:`tutorial01` builds the first real page of the Notes application.
   :doc:`/content/topics/project-layout` explains where files belong as the project grows.
