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

URL Parameters in Context
-------------------------

URL parameters are automatically passed to context functions:

.. code-block:: python

   # pages/user/[username]/page.py
   template = """
   <h1>User Profile: {{ username }}</h1>
   <p>User ID: {{ user_id }}</p>
   <p>Profile Views: {{ profile_views }}</p>
   """

   @context
   def get_user_profile(request, username):
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

- **layouts/**: Context inheritance examples
- **pages/**: Context function usage examples
- **components/**: Component-based context examples

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
