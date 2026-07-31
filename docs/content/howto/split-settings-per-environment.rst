.. _howto-split-settings-per-environment:

Split settings per environment
==============================

Problem
-------

Development and production need different values for ``DEBUG``, the database, and parts of ``NEXT_FRAMEWORK``, and a single ``settings.py`` cannot hold both without conditionals.

Solution
--------

Replace ``settings.py`` with a ``settings`` package.
A ``base.py`` module holds every shared value, including the full ``NEXT_FRAMEWORK`` block.
A ``dev.py`` and a ``prod.py`` module import from ``base`` and override only what differs.
The ``DJANGO_SETTINGS_MODULE`` environment variable selects one per process.

Walkthrough
-----------

Create the settings package
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Delete ``config/settings.py`` and create a ``config/settings/`` directory with an empty ``__init__.py``.
Place ``base.py``, ``dev.py``, and ``prod.py`` beside it.

.. code-block:: text
   :caption: project layout

   config/
     settings/
       __init__.py
       base.py
       dev.py
       prod.py
     urls.py
     wsgi.py

Define the shared base
~~~~~~~~~~~~~~~~~~~~~~

``base.py`` holds every value common to all environments.
Define ``NEXT_FRAMEWORK`` here in full so the framework configuration has one source of truth.

.. code-block:: python
   :caption: config/settings/base.py

   from pathlib import Path

   BASE_DIR = Path(__file__).resolve().parent.parent.parent

   INSTALLED_APPS = [
       "django.contrib.contenttypes",
       "django.contrib.auth",
       "django.contrib.staticfiles",
       "next",
       "notes",
   ]

   ROOT_URLCONF = "config.urls"

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           },
       ],
   }

Override for development
~~~~~~~~~~~~~~~~~~~~~~~~

``dev.py`` imports everything from ``base`` with a star import, then overrides the values that differ.
``DEBUG`` is on and the database is a local SQLite file.

.. code-block:: python
   :caption: config/settings/dev.py

   from config.settings.base import *  # noqa: F403

   DEBUG = True

   ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

   DATABASES = {
       "default": {
           "ENGINE": "django.db.backends.sqlite3",
           "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
       },
   }

Development keeps the framework defaults, so ``dev.py`` leaves ``NEXT_FRAMEWORK`` untouched.
The default ``STRICT_CONTEXT`` value ``False`` keeps local rendering alive when a context processor fails.
The copy-and-patch pattern for a per-environment key is shown in the production module below.

Override for production
~~~~~~~~~~~~~~~~~~~~~~~

``prod.py`` follows the same shape.
``DEBUG`` is off, hosts and secrets come from the environment, and the strict context check is on.

.. code-block:: python
   :caption: config/settings/prod.py

   import os
   from config.settings.base import *  # noqa: F403

   DEBUG = False

   ALLOWED_HOSTS = os.environ["ALLOWED_HOSTS"].split(",")

   SECRET_KEY = os.environ["SECRET_KEY"]

   NEXT_FRAMEWORK = {**NEXT_FRAMEWORK}  # noqa: F405
   NEXT_FRAMEWORK["STRICT_CONTEXT"] = True

Override a single ``NEXT_FRAMEWORK`` key without rewriting the whole block by copying it from ``base`` and patching the copy.
Because ``{**NEXT_FRAMEWORK}`` is a shallow copy, mutate only flat keys this way.
A flat key such as ``STRICT_CONTEXT`` is copied and reassigned as shown here.
For nested backend lists use ``extend_default_backend`` or ``copy.deepcopy``.
See :doc:`extend-a-default-backend` for the backend-list case.

Select a module per process
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``manage.py`` and ``wsgi.py`` read ``DJANGO_SETTINGS_MODULE`` from the environment.
Default it to ``config.settings.dev`` so local commands need no extra flag.

.. code-block:: python
   :caption: manage.py

   import os

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

A production process exports ``DJANGO_SETTINGS_MODULE=config.settings.prod`` before starting the server.

.. note::

   ``config/wsgi.py`` and ``config/asgi.py`` carry the same ``os.environ.setdefault(..., "config.settings.dev")`` line.
   A server started without the variable also defaults to development.
   See :doc:`/content/deployment/wsgi-asgi`.

Verification
------------

Run the framework system checks under each module.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check
   DJANGO_SETTINGS_MODULE=config.settings.prod uv run python manage.py check

Both runs report no errors.
The development run has ``DEBUG`` on and the production run has it off.
The production run carries the ``STRICT_CONTEXT`` override, the development run keeps the default.

See also
--------

.. seealso::

   :doc:`/content/ref/settings` for every ``NEXT_FRAMEWORK`` key.
   :doc:`/content/deployment/settings` for production hardening.
