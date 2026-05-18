.. _howto-split-settings-per-environment:

Split Settings per Environment
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

Create the Settings Package
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

Define the Shared Base
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
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "DIRS": [],
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
               "OPTIONS": {"context_processors": []},
           },
       ],
   }

Override for Development
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

Override a single ``NEXT_FRAMEWORK`` key without rewriting the whole block by copying it from ``base`` and patching the copy.

.. code-block:: python
   :caption: config/settings/dev.py

   NEXT_FRAMEWORK = {**NEXT_FRAMEWORK}  # noqa: F405
   NEXT_FRAMEWORK["STRICT_CONTEXT"] = False

``extend_default_backend`` patches the backend-list keys.
A flat key such as ``STRICT_CONTEXT`` is copied and reassigned as shown here.
See :doc:`extend-a-default-backend` for the backend-list case.

Override for Production
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

Select a Module per Process
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``manage.py`` and ``wsgi.py`` read ``DJANGO_SETTINGS_MODULE`` from the environment.
Default it to ``config.settings.dev`` so local commands need no extra flag.

.. code-block:: python
   :caption: manage.py

   import os


   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

A production process exports ``DJANGO_SETTINGS_MODULE=config.settings.prod`` before starting the server.

Verification
------------

Run the framework system checks under each module.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check
   DJANGO_SETTINGS_MODULE=config.settings.prod uv run python manage.py check

Both runs report no errors.
The development run has ``DEBUG`` on, the production run has it off, and ``NEXT_FRAMEWORK`` carries the per-environment override in each.

See Also
--------

.. seealso::

   :doc:`/content/ref/settings` for every ``NEXT_FRAMEWORK`` key.
   :doc:`/content/deployment/settings` for production hardening.
