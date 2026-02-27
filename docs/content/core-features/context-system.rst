Context System
==============

next.dj provides a flexible context system for passing data to templates, supporting global, local, and keyed context functions with inheritance capabilities.

Context Function Types
----------------------

**Unkeyed Context Functions** (return dictionaries):

.. code-block:: python

   @context
   def get_user_info(request):
       return {
           "user_name": request.user.username if request.user.is_authenticated else "Guest",
           "greeting": "Hello!"
       }

**Keyed Context Functions** (return single values):

.. code-block:: python

   @context("username")
   def get_username(request):
       return request.user.username

   @context("stats")
   def get_user_stats(request):
       return {
           "posts": 42,
           "comments": 128,
           "likes": 1024
       }

Context Inheritance
-------------------

Context functions can be inherited by child pages using layouts with the ``inherit_context=True`` parameter:

.. code-block:: python

   # pages/layout/page.py
   @context("site_name", inherit_context=True)
   def get_site_name(request):
       return "My Site"

   @context("navigation", inherit_context=True)
   def get_navigation(request):
       return {
           "nav_items": [
               {"name": "Home", "url": "/"},
               {"name": "About", "url": "/about/"},
               {"name": "Contact", "url": "/contact/"}
           ]
       }

Child pages will automatically have access to inherited context data.

Global Context (Context Processors)
-----------------------------------

Use Django's context processors for global template variables:

.. code-block:: python

   # settings.py
   NEXT_PAGES = [
       {
           'BACKEND': 'next.urls.FileRouterBackend',
           'APP_DIRS': True,
           'OPTIONS': {
               'context_processors': [
                   'django.template.context_processors.request',
                   'django.template.context_processors.debug',
                   'myapp.context_processors.global_context',
               ],
           },
       },
   ]

.. code-block:: python

   # myapp/context_processors.py
   def global_context(request):
       return {
           'SITE_NAME': 'My Site',
           'SITE_URL': 'https://example.com',
           'DEBUG': settings.DEBUG,
       }

Context Priority
----------------

Context data is merged with the following priority (highest to lowest):

1. **Template variables** (passed directly to render)
2. **Context functions** (from the current page)
3. **Inherited context** (from layout pages with ``inherit_context=True``)
4. **Context processors** (global Django context)
5. **Default context** (basic request data)

Dependency Injection
--------------------

Context functions receive only the arguments they declare: the framework resolves
parameters from the request context (request, URL kwargs, form) by inspecting
the function signature. Declare ``request: HttpRequest``, ``id: int``, etc.; no
``*args`` or ``**kwargs`` needed.

Injecting context by name: Context and Depends
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

You can **explicitly** request values by name using two markers (FastAPI-style,
via **default parameter values**):

- **``Context("key")``** — Injects the value of a **context variable** already
  in the current request: from a parent layout (e.g. ``@context("key", inherit_context=True)``)
  or from an earlier context function on the same page. The ``key`` is the name
  of the variable (e.g. ``"custom_variable"``, ``"app_greeting"``).

- **``Depends("name")``** — Injects the result of a **registered dependency**
  (a callable registered with ``@resolver.dependency("name")``). Use for app-wide
  data (theme, config, current user) that is not tied to the page/layout hierarchy.

**Example: subpage receiving layout global + parent context (see ``examples/layouts/``):**

.. code-block:: python

   # Layout page (e.g. pages/page.py): register global dependency and inherited context
   from next.deps import resolver
   from next.pages import context

   @resolver.dependency("layout_theme")
   def get_layout_theme():
       return {"name": "Bootstrap", "version": "5.0"}

   @context("custom_variable", inherit_context=True)
   def custom_variable_context_with_inherit(request):
       return "Hello from layout!"

   # Child page (e.g. pages/guides/page.py): inject both by name
   from next.deps import Depends
   from next.pages import Context, context

   @context("layout_theme_data")
   def guides_theme(layout_theme: dict[str, str] | None = Depends("layout_theme")):
       return layout_theme

   @context("parent_context_data")
   def guides_parent(custom_variable: str | None = Context("custom_variable")):
       return custom_variable

In the template you can then use ``{{ layout_theme_data }}`` and ``{{ parent_context_data }}``.
You can also rely on **parameter name**: if the parameter has the same name as a
context key (e.g. ``app_greeting: str`` when ``@context("app_greeting")`` exists),
the value is injected without using ``Context(...)`` (see ``ContextByNameProvider``).

For full details, custom providers, and API reference, see :doc:`/content/dependency-injection`.

URL Parameters in Context
-------------------------

URL parameters are automatically passed to context functions when you declare
them by name:

.. code-block:: python

   # pages/user/[username]/page.py
   template = """
   <h1>User Profile: {{ username }}</h1>
   <p>User ID: {{ user_id }}</p>
   <p>Profile Views: {{ profile_views }}</p>
   """

   @context
   def get_user_profile(request: HttpRequest, username: str):
       # username is automatically passed from the URL
       return {
           "username": username,
           "user_id": hash(username) % 1000,
           "profile_views": 42  # In real app, fetch from database
       }

Context Function Registration
-----------------------------

The system automatically detects the calling file and associates context functions with it:

.. code-block:: python

   # pages/dashboard/page.py
   @context
   def get_dashboard_data(request):
       return {
           "total_users": 1250,
           "active_sessions": 42
       }

   @context("recent_activity")
   def get_recent_activity(request):
       return [
           {"action": "User registered", "time": "2 minutes ago"},
           {"action": "Post published", "time": "5 minutes ago"}
       ]

Context Collection Process
--------------------------

When a page is rendered, the system:

1. **Collects inherited context** from layout directories (lower priority)
2. **Collects context from current file** (higher priority - can override inherited)
3. **Merges unkeyed functions** (dictionaries are merged)
4. **Stores keyed functions** (single values under their key)
5. **Applies context processors** (global Django context)
6. **Applies template variables** (highest priority)

Error Handling
--------------

Context function errors are handled gracefully:

.. code-block:: python

   @context
   def get_user_data(request):
       try:
           if request.user.is_authenticated:
               return {
                   "user_name": request.user.username,
                   "user_email": request.user.email
               }
           else:
               return {"user_name": "Guest"}
       except Exception as e:
           # Log the error and return safe defaults
           logger.error(f"Error in get_user_data: {e}")
           return {"user_name": "Unknown"}

Context Caching
---------------

Context functions are called on every request. For expensive operations, consider caching:

.. code-block:: python

   from django.core.cache import cache

   @context
   def get_expensive_data(request):
       cache_key = "expensive_data"
       data = cache.get(cache_key)
       
       if data is None:
           # Expensive operation
           data = perform_expensive_calculation()
           cache.set(cache_key, data, 300)  # Cache for 5 minutes
       
       return {"expensive_data": data}

Validation Checks
-----------------

The system includes validation checks for context functions:

**Context Function Validation** (``check_context_functions``):
- Validates that context functions decorated with ``@context`` (without key) always return a dictionary
- Ensures proper return types for different context function patterns
- Can be disabled by setting ``NEXT_PAGES_OPTIONS.check_context_return_types = False``

**Page Function Validation** (``check_page_functions``):
- Ensures page.py files have valid render functions or proper template definitions
- Validates function signatures, return types, and argument handling
- Prevents runtime errors during page rendering

**Missing Page Content Validation** (``check_missing_page_content``):
- Checks for page.py files that have no content (no template, no render function)
- Validates that pages have either template variable, template.djx file, layout.djx file, or render function
- Can be disabled by setting ``NEXT_PAGES_OPTIONS.check_missing_page_content = False``

Run validation checks:

.. code-block:: bash

   python manage.py check

Examples
--------

See the ``examples/`` directory in the source code for complete working examples:

- **layouts/**: Context inheritance, **Context("key")** (parent context by name), and
  **Depends("name")** (layout-level global dependency) — see ``layouts/pages/page.py``
  and ``layouts/pages/guides/page.py``.
- **pages/**: Context function usage and keyed context (e.g. ``@context("app_greeting")``).

Best Practices
--------------

1. **Keep context functions focused**: Each function should have a single responsibility
2. **Handle errors gracefully**: Always provide fallback values
3. **Use meaningful names**: Make context data easy to understand
4. **Consider performance**: Cache expensive operations
5. **Test context functions**: Ensure they work correctly
6. **Use inheritance wisely**: Mark context functions with ``inherit_context=True`` only when needed
7. **Follow naming conventions**: Use consistent function and variable names
8. **Document context data**: Help other developers understand what's available
