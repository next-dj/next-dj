next.dj Documentation
=====================

A next-gen framework based on Django for cool engineers who love to do complex things!

What is next.dj?
----------------

``next.dj`` is a **full-stack Django framework** that brings file-based routing and modern web development patterns to Django applications. It provides a Next.js-like development experience while maintaining Django's powerful backend capabilities.

**Current Status: Active Development Phase**

next.dj is currently in active development. While the core features are stable and production-ready, many advanced features are still being developed:

**Available Now:**
* File-based routing system
* DJX template system with layout inheritance
* Context management system
* Comprehensive validation checks

**Coming Soon:**
* Component system for reusable UI elements
* Suspense for async data loading
* Forms with fast partial page reloads
* Integration with JavaScript frameworks
* WebSocket support out of the box

**We Need Developers!**

next.dj is actively seeking contributors! If you're interested in modern web development, Django, or want to help shape the future of Python web frameworks, we'd love to have you on board. See our :doc:`content/development/contributing` guide for how to get started.

Key Features
------------

* **File-based Routing**: Create pages by simply adding ``page.py`` files to your project structure
* **DJX Templates**: Custom template system with powerful layout inheritance
* **Context Management**: Flexible context system for passing data to templates
* **Multiple Configurations**: Support for multiple NEXT_PAGES configurations
* **Comprehensive Validation**: Built-in checks for configuration and structure validation

Quick Start
-----------

Installation
~~~~~~~~~~~~

.. code-block:: bash

   pip install next-dj

Basic Usage
~~~~~~~~~~~

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

3. Create a page by adding a ``page.py`` file:

.. code-block:: python

   # pages/home/page.py
   template = """
   <h1>Welcome to next.dj!</h1>
   <p>Hello, {{ name }}!</p>
   """

   @context
   def get_name(request):
       return {"name": "World"}

That's it! Your page will be available at ``/home/``.

Examples
--------

See the ``examples/`` directory in the source code for complete working examples:

- **file-routing/**: Basic file-based routing examples
- **pages/**: Page creation and template examples  
- **layouts/**: Layout inheritance examples

Contents
--------

.. toctree::
   :maxdepth: 2

   content/getting-started/index
   content/core-features/index
   content/api/index
   content/development/index

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
