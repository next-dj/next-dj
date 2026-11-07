.. _intro-install:

Installation
============

This page installs next.dj, configures the page backend, and verifies that the file router is reachable.
It scaffolds a new Django project on the way, so a project that already exists starts at :ref:`intro-install-installed-apps` and keeps its own layout.
By the end you have a project that renders a single page from the filesystem and is ready for :doc:`tutorial01`.

Requirements
------------

- Python 3.12 or newer (3.12, 3.13, 3.14, 3.15 tested).
- Django 5.2, 6.0, or 6.1.
- Python 3.15 requires Django 6.1.3 or newer.
  Django 5.2 (5.2.8 or newer on Python 3.14) and 6.0 support Python 3.12 to 3.14.
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

The test helpers of ``next.testing`` need the ``testing`` extra, which a project adds to its development dependencies.

.. code-block:: bash
   :caption: shell

   uv add --dev "next.dj[testing]"

A project that shares realtime topics across processes adds the ``broker`` extra with ``uv add "next.dj[broker]"``, see :doc:`/content/howto/fan-out-across-processes-with-redis`.
The WebSocket transport in development needs ``uv add "uvicorn[standard]"``, see :doc:`/content/howto/stream-over-websockets`.

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

.. _intro-install-app-order:

.. note::

   ``next`` sits above ``django.contrib.staticfiles`` because both apps ship a ``runserver`` command and Django runs the one of the app listed first, as :ref:`django:overriding-commands` describes.
   The ``runserver`` of ``next`` serves the project over ASGI with the realtime WebSocket once uvicorn with a WebSocket library is installed, for example through ``uv add "uvicorn[standard]"``, and over WSGI otherwise.
   With ``django.contrib.staticfiles`` listed first the development server stays on WSGI and the pages silently speak SSE, which ``manage.py check`` reports as ``next.W148``.

Open ``config/settings.py`` and register both ``next`` and your application in :doc:`INSTALLED_APPS <django:ref/settings>`.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django.contrib.sessions",
       "django.contrib.messages",
       "next",
       "django.contrib.staticfiles",
       "notes",
   ]

The list above is what the project scaffolded in the previous section needs.
A project that already exists keeps its own entries and adds a single ``next`` entry.
The list drops ``django.contrib.admin`` because next.dj does not require the admin site, so a project that uses the admin keeps that entry in place.

The ``next`` app registers system checks, template tag builtins, autoreload hooks, and static file collectors at startup.

Configure NEXT_FRAMEWORK
------------------------

Tell next.dj where to look for pages and components.
With ``APP_DIRS`` set to ``True`` and no ``DIRS``, each installed app carries its own ``pages/`` tree, and a ``_components/`` folder placed inside that tree holds the app's components.
The file router walk registers each ``_components/`` folder it encounters while walking the page tree, so the component backend needs no separate ``APP_DIRS`` flag.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
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
The entry leaves out ``DIRS`` and ``OPTIONS``, which then read as an empty list and an empty mapping, see :ref:`ref-settings` for both keys.

Keep ``django.template.context_processors.request`` in the ``OPTIONS`` of your ``TEMPLATES`` setting.
A fresh ``django-admin startproject`` already includes it, and ``manage.py check`` reports ``next.E019`` if it is missing.

Include the router
------------------

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

A project built exactly as this page prescribes reports a clean run.

.. code-block:: text
   :caption: shell output

   System check identified no issues (0 silenced).

The command exits with status zero, and every message it does print names both the configuration key it read and the recommended fix.
The partial asset version needs no settings of its own, because it is derived from the staticfiles manifest when the project has one and falls back to a stable string when it does not, see :doc:`/content/ref/settings`.

Among its reports ``next.E017`` names a ``page.py`` that raises while importing, ``next.E084`` names the same for a ``component.py``, and ``next.E072`` names a composed page template that fails to compile.
Seven other framework checks carry Django's ``deploy`` flag and stay silent until the deployment run asks for them.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy

.. _intro-install-project:

The project canon
-----------------

next.dj ships no project template, because a project needs nothing beyond what Django's own ``startproject`` and ``startapp`` write and the few lines below.
The steps above already hold the two that are required.

- ``"next"`` in ``INSTALLED_APPS``.
- ``path("", include("next.urls"))`` as the last entry of the root URLconf.

The rest is optional and follows what the project needs.

Every page behind a login.
   Set ``NEXT_FRAMEWORK["LOGIN_REQUIRED"]`` to ``True``, or list Django's :class:`~django.contrib.auth.middleware.LoginRequiredMiddleware` right after ``AuthenticationMiddleware``, which also closes the Django views of the project, and set ``LOGIN_URL``.
   A page anyone may open, the sign-in page among them, then declares ``page.public()`` in its ``page.py``, see :doc:`/content/howto/require-login-on-pages`.

   .. code-block:: python
      :caption: config/settings.py

      MIDDLEWARE = [
          "django.middleware.security.SecurityMiddleware",
          "django.contrib.sessions.middleware.SessionMiddleware",
          "django.middleware.common.CommonMiddleware",
          "django.middleware.csrf.CsrfViewMiddleware",
          "django.contrib.auth.middleware.AuthenticationMiddleware",
          "django.contrib.auth.middleware.LoginRequiredMiddleware",
          "django.contrib.messages.middleware.MessageMiddleware",
          "django.middleware.clickjacking.XFrameOptionsMiddleware",
      ]

      LOGIN_URL = "/login/"

Object permissions.
   List ``ObjectPermissionBackend`` beside ``ModelBackend``, so ``user.has_perm(perm, obj)`` reaches the predicates every ``permissions.py`` registers, see :doc:`/content/topics/permissions`.

   .. code-block:: python
      :caption: config/settings.py

      AUTHENTICATION_BACKENDS = [
          "django.contrib.auth.backends.ModelBackend",
          "next.permissions.ObjectPermissionBackend",
      ]

One layout for every page.
   Name a project-level page root in ``DIRS`` of the ``PAGE_BACKENDS`` entry and put a ``layout.djx`` in it, which wraps the pages of every application tree, see :doc:`/content/topics/layouts`.

   .. code-block:: python
      :caption: config/settings.py

      NEXT_FRAMEWORK = {
          "PAGE_BACKENDS": [
              {
                  "BACKEND": "next.urls.FileRouterBackend",
                  "DIRS": [BASE_DIR / "chrome"],
                  "APP_DIRS": True,
                  "PAGES_DIR": "pages",
              }
          ],
      }

   .. code-block:: jinja
      :caption: chrome/layout.djx

      <!doctype html>
      <html lang="en">
        <head>
          <meta charset="utf-8">
          {% metadata %}
          {% collect_styles %}
        </head>
        <body>
          {% template %}
          {% collect_scripts %}
        </body>
      </html>

   ``{% metadata %}`` renders the head tags of the page, ``{% collect_styles %}`` and ``{% collect_scripts %}`` the co-located assets, and ``{% template %}`` the page itself.

An application starts from a plain ``uv run python manage.py startapp shop`` and its entry in ``INSTALLED_APPS``, and gains the files of :ref:`topics-project-layout-files` as it needs them.

Next steps
----------

The environment is ready for the tutorial.

.. seealso::

   :doc:`tutorial01` builds the first real page of the Notes application.
   :doc:`from-django` maps the Django idioms this settings block replaces onto their next.dj shapes.
   :doc:`/content/topics/project-layout` explains where files belong as the project grows.
