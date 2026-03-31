Getting started
===============

.. _getting-started:

This page walks you through installing next.dj and running your first page.

Installation
------------

Requirements: Python 3.12+, Django 4.2+.

.. tab-set::

   .. tab-item:: uv

      .. code-block:: bash

         uv add next.dj

   .. tab-item:: pip

      .. code-block:: bash

         pip install next.dj

Install from source
~~~~~~~~~~~~~~~~~~~~

.. tab-set::

   .. tab-item:: uv

      .. code-block:: bash

         git clone https://github.com/next-dj/next-dj.git
         cd next-dj
         uv sync --locked --dev

   .. tab-item:: pip

      .. code-block:: bash

         git clone https://github.com/next-dj/next-dj.git
         cd next-dj
         pip install -e .

Development Installation
------------------------

.. tab-set::

   .. tab-item:: uv

      .. code-block:: bash

         git clone https://github.com/next-dj/next-dj.git
         cd next-dj
         uv sync --locked --dev

   .. tab-item:: pip

      .. code-block:: bash

         git clone https://github.com/next-dj/next-dj.git
         cd next-dj
         pip install -e .

Development tools (pytest, ruff, mypy, and others) are listed under ``[dependency-groups] dev`` in ``pyproject.toml``. With ``uv`` use ``uv sync --locked --dev``. With pip alone, install those packages as needed for your workflow.

Django Setup
------------

1. Add ``next`` to your ``INSTALLED_APPS``:

.. code-block:: python

   INSTALLED_APPS = [
       'django.contrib.admin',
       'django.contrib.auth',
       'django.contrib.contenttypes',
       'django.contrib.sessions',
       'django.contrib.messages',
       'django.contrib.staticfiles',
       'next',  # Add this
   ]

2. Include the URLs in your main ``urls.py``:

.. code-block:: python

   from django.urls import path, include

   urlpatterns = [
       path('', include('next.urls')),
   ]

3. Optionally configure ``NEXT_FRAMEWORK`` in your settings:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "PAGES_DIR": "pages",
               "APP_DIRS": True,
               "OPTIONS": {
                   "context_processors": [
                       "myapp.context_processors.global_context",
                   ],
               },
           },
       ],
   }

That's it! You're ready to start building with next.dj.

Next
----

:doc:`file-router` — Learn how file-based routing maps URLs to your ``page.py`` files.
