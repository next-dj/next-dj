.. _howto-customize-error-pages:

Customize 404 and 500 Pages
===========================

Problem
-------

A missing route or an unhandled exception should show a branded page that matches the rest of the site instead of Django's plain default.

Solution
--------

Error handling sits in Django's URL resolver, not in the file router.
The file router has no error-page convention of its own.
A ``page.py`` that raises ``Http404`` or an unhandled exception routes through Django's own handlers.
Define ``handler404`` and ``handler500`` in the root URLconf, or rely on the convention that Django renders ``404.html`` and ``500.html`` from the template directories when ``DEBUG`` is off.
The file router include stays unchanged.

Walkthrough
-----------

Turn Off Debug
~~~~~~~~~~~~~~

Custom error pages render only when ``DEBUG`` is off.
With ``DEBUG`` on, Django shows its own traceback page instead.

.. code-block:: python
   :caption: config/settings.py

   DEBUG = False

   ALLOWED_HOSTS = ["127.0.0.1", "localhost"]

Add the Error Templates
~~~~~~~~~~~~~~~~~~~~~~~

Django looks for ``404.html`` and ``500.html`` at the root of a configured template directory.
These are plain Django templates rendered by the resolver, so keep them self-contained.
A ``500.html`` template renders with an empty context because the failure may have happened before any context was built.

.. code-block:: python
   :caption: config/settings.py

   TEMPLATES = [
       {
           "BACKEND": "django.template.backends.django.DjangoTemplates",
           "DIRS": [BASE_DIR / "templates"],
           "APP_DIRS": True,
           "OPTIONS": {"context_processors": []},
       },
   ]

.. code-block:: html
   :caption: templates/404.html

   <!doctype html>
   <html lang="en">
     <head><title>Page not found</title></head>
     <body>
       <h1>That page does not exist</h1>
       <p><a href="/">Return to the homepage.</a></p>
     </body>
   </html>

.. code-block:: html
   :caption: templates/500.html

   <!doctype html>
   <html lang="en">
     <head><title>Server error</title></head>
     <body>
       <h1>Something went wrong</h1>
       <p>The team has been notified. Try again shortly.</p>
     </body>
   </html>

Point the Handlers at a View
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For full control, name a :doc:`custom error handler <django:topics/http/views>` in the root URLconf.
The dotted strings sit beside the file router include and Django resolves them when no route matches or a view raises.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path


   urlpatterns = [
       path("", include("next.urls")),
   ]

   handler404 = "config.errors.not_found"
   handler500 = "config.errors.server_error"

A ``handler404`` view receives the request and the raised exception.
A ``handler500`` view receives only the request.

.. code-block:: python
   :caption: config/errors.py

   from django.shortcuts import render


   def not_found(request, exception):
       """Render the branded 404 page."""
       return render(request, "404.html", status=404)


   def server_error(request):
       """Render the branded 500 page."""
       return render(request, "500.html", status=500)

Verification
------------

Run the server with ``DEBUG`` off and request a route that does not exist.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver --insecure

Visiting an unknown path returns the branded ``404`` page with status ``404``.
A view that raises an unhandled exception returns the branded ``500`` page with status ``500``.

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for how routes resolve to pages.
   :doc:`/content/howto/require-login-on-pages` for redirecting unauthorized requests.
