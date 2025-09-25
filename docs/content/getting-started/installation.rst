Installation
============

Requirements
------------

* Python 3.12+
* Django 4.2+

Installation
------------

Install next.dj using pip:

.. code-block:: bash

   pip install next-dj

Or using uv (recommended for this project):

.. code-block:: bash

   uv add next-dj

Install from source using pip:

.. code-block:: bash

   git clone https://github.com/next-dj/next-dj.git
   cd next-dj
   pip install -e .

Install from source using uv:

.. code-block:: bash

   git clone https://github.com/next-dj/next-dj.git
   cd next-dj
   uv sync --dev
   uv pip install -e .

Development Installation
------------------------

For development, install with development dependencies using pip:

.. code-block:: bash

   git clone https://github.com/next-dj/next-dj.git
   cd next-dj
   pip install -e ".[dev]"

For development using uv (recommended):

.. code-block:: bash

   git clone https://github.com/next-dj/next-dj.git
   cd next-dj
   uv sync --dev
   uv pip install -e .

This will install additional development tools like pytest, ruff, and mypy.

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

3. Optionally configure ``NEXT_PAGES`` in your settings:

.. code-block:: python

   NEXT_PAGES = [
       {
           'BACKEND': 'next.urls.FileRouterBackend',
           'APP_DIRS': True,
           'OPTIONS': {
               'context_processors': [
                   'myapp.context_processors.global_context',
               ],
           },
       },
   ]

That's it! You're ready to start building with next.dj.
