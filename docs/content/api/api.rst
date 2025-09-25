API Reference
=============

This section provides detailed API documentation for the next.dj framework.

Core Modules
------------

.. toctree::
   :maxdepth: 2

   api/pages
   api/urls
   api/checks

Pages Module
~~~~~~~~~~~~

The pages module provides the core page rendering and template management functionality.

.. automodule:: next.pages
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

URLs Module
~~~~~~~~~~~

The urls module handles file-based URL pattern generation and routing.

.. automodule:: next.urls
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Checks Module
~~~~~~~~~~~~~

The checks module provides Django system checks for next.dj configuration.

.. automodule:: next.checks
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Template Loaders
----------------

next.dj provides several template loaders for different template sources.

PythonTemplateLoader
~~~~~~~~~~~~~~~~~~~~

Loads templates from Python modules that define a 'template' attribute.

.. autoclass:: next.pages.PythonTemplateLoader
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

DjxTemplateLoader
~~~~~~~~~~~~~~~~~

Loads templates from .djx files located alongside page.py files.

.. autoclass:: next.pages.DjxTemplateLoader
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

LayoutTemplateLoader
~~~~~~~~~~~~~~~~~~~~

Loads layout templates from layout.djx files in parent directories.

.. autoclass:: next.pages.LayoutTemplateLoader
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Context Management
------------------

ContextManager
~~~~~~~~~~~~~~

Manages context functions and their execution for page templates.

.. autoclass:: next.pages.ContextManager
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Layout Management
-----------------

LayoutManager
~~~~~~~~~~~~~

Manages layout template discovery and inheritance for page templates.

.. autoclass:: next.pages.LayoutManager
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

URL Pattern Generation
----------------------

URLPatternParser
~~~~~~~~~~~~~~~~

Converts file-based URL paths to Django URL patterns with parameter extraction.

.. autoclass:: next.urls.URLPatternParser
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Router Backends
---------------

RouterBackend
~~~~~~~~~~~~~

Abstract interface for URL pattern generation backends.

.. autoclass:: next.urls.RouterBackend
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

FileRouterBackend
~~~~~~~~~~~~~~~~~

File-based URL pattern generation backend.

.. autoclass:: next.urls.FileRouterBackend
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Router Factory
--------------

RouterFactory
~~~~~~~~~~~~~

Factory for creating router backend instances from configuration.

.. autoclass:: next.urls.RouterFactory
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Router Manager
--------------

RouterManager
~~~~~~~~~~~~~

Centralized manager for multiple router backends and their configurations.

.. autoclass:: next.urls.RouterManager
   :members:
   :undoc-members:
   :show-inheritance:
   :no-index:

Global Instances
----------------

page
~~~~

Global singleton instance for application-wide page management.

.. autodata:: next.pages.page
   :no-index:

context
~~~~~~~

Convenience alias for the context decorator.

.. autodata:: next.pages.context
   :no-index:

router_manager
~~~~~~~~~~~~~~

Global router manager instance for application-wide URL pattern management.

.. autodata:: next.urls.router_manager
   :no-index:

urlpatterns
~~~~~~~~~~~

Django URL configuration.

.. autodata:: next.urls.urlpatterns
   :no-index:

Configuration
-------------

NEXT_PAGES Setting
~~~~~~~~~~~~~~~~~~

Configure next.dj behavior in your Django settings.

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

Backend Options
~~~~~~~~~~~~~~~

FileRouterBackend Options
^^^^^^^^^^^^^^^^^^^^^^^^^

- ``APP_DIRS``: Whether to scan Django app directories (default: True)
- ``context_processors``: List of context processor paths

Custom Backend Options
^^^^^^^^^^^^^^^^^^^^^^

Custom backends can define their own options in the ``OPTIONS`` dictionary.

Examples
--------

Basic Page
~~~~~~~~~~

.. code-block:: python

   from next.pages import page, context

   # Define template
   template = """
   <h1>{{ title }}</h1>
   <p>{{ content }}</p>
   """

   # Define context function
   @context
   def get_page_data(request):
       return {
           "title": "My Page",
           "content": "This is my page content"
       }

   # Register template
   page.register_template(Path(__file__), template)

Dynamic Page
~~~~~~~~~~~~

.. code-block:: python

   from next.pages import page, context

   # Define template
   template = """
   <h1>User: {{ username }}</h1>
   <p>User ID: {{ user_id }}</p>
   """

   # Define context function with URL parameter
   @context
   def get_user_data(request, username):
       return {
           "username": username,
           "user_id": hash(username) % 1000
       }

   # Register template
   page.register_template(Path(__file__), template)

Custom Render Function
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from django.http import JsonResponse

   def render(request):
       data = {"message": "Hello from API"}
       return JsonResponse(data)

Custom Template Loader
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.pages import TemplateLoader

   class DatabaseTemplateLoader(TemplateLoader):
       def can_load(self, file_path):
           return self._template_exists_in_db(file_path)

       def load_template(self, file_path):
           return self._get_template_from_db(file_path)

   # Register the loader
   page._template_loaders.append(DatabaseTemplateLoader())

Custom Router Backend
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.urls import RouterBackend
   from django.urls import URLPattern, path

   class DatabaseRouterBackend(RouterBackend):
       def generate_urls(self):
           urls = []
           for route in self.get_routes_from_database():
               urls.append(path(route.path, route.view, name=route.name))
           return urls

   # Register the backend
   from next.urls import RouterFactory
   RouterFactory.register_backend('myapp.routing.DatabaseRouterBackend', DatabaseRouterBackend)

Error Handling
--------------

next.dj provides several error handling mechanisms:

Template Loading Errors
~~~~~~~~~~~~~~~~~~~~~~~

When template loading fails, the system logs a warning and continues with other loaders.

Context Function Errors
~~~~~~~~~~~~~~~~~~~~~~~

Context function errors are logged but don't stop page rendering. Provide fallback values.

URL Pattern Errors
~~~~~~~~~~~~~~~~~~

URL pattern generation errors are logged and the problematic pattern is skipped.

Configuration Errors
~~~~~~~~~~~~~~~~~~~~

Configuration errors are caught during system checks and reported to Django.

Debugging
---------

Enable Debug Logging
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   LOGGING = {
       'version': 1,
       'disable_existing_loggers': False,
       'handlers': {
           'console': {
               'class': 'logging.StreamHandler',
           },
       },
       'loggers': {
           'next.pages': {
               'handlers': ['console'],
               'level': 'DEBUG',
               'propagate': True,
           },
           'next.urls': {
               'handlers': ['console'],
               'level': 'DEBUG',
               'propagate': True,
           },
       },
   }

Check Generated URLs
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.urls import router_manager

   # Print all generated URL patterns
   for pattern in router_manager:
       print(f"Pattern: {pattern.pattern}")
       print(f"Name: {pattern.name}")
       print(f"View: {pattern.callback}")

Check Template Registry
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   from next.pages import page

   # Print all registered templates
   for file_path, template in page._template_registry.items():
       print(f"File: {file_path}")
       print(f"Template: {template[:100]}...")

Performance Considerations
----------------------------

Template Caching
~~~~~~~~~~~~~~~~

Templates are automatically cached for performance. The cache is cleared when files are modified.

URL Pattern Caching
~~~~~~~~~~~~~~~~~~~

URL patterns are cached and only regenerated when necessary.

Context Function Optimization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Context functions are called on every request. Consider caching expensive operations.

Memory Usage
~~~~~~~~~~~~

The system maintains registries for templates, contexts, and layouts. Monitor memory usage in production.

Best Practices
--------------

1. **Use appropriate template loaders**: Choose the right loader for your use case
2. **Handle errors gracefully**: Always provide fallback values
3. **Test thoroughly**: Ensure all routes and templates work correctly
4. **Monitor performance**: Watch for memory leaks and slow operations
5. **Document custom extensions**: Help other developers understand your code
