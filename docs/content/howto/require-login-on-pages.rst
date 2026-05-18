.. _howto-require-login-on-pages:

Require Login on File-Routed Pages
==================================

Problem
-------

Every page under the file router must be visible only to authenticated users, and anonymous visitors should land on the login page.

Solution
--------

The file router mounts as a single :func:`~django.urls.include` in the root URLconf, so it has no per-view decorator surface.
Guard the routes with project :doc:`middleware <django:topics/http/middleware>` that checks :attr:`request.user.is_authenticated <django:django.contrib.auth.models.User.is_authenticated>`.
It redirects anonymous requests.
A page or component callable that needs the user reads it back through the request.

Walkthrough
-----------

Write the Guard Middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~

The middleware lets the login page and static assets through, and redirects every other anonymous request to the login URL with a ``next`` parameter.

.. code-block:: python
   :caption: notes/middleware.py

   from django.conf import settings
   from django.shortcuts import redirect
   from django.urls import reverse

   class LoginRequiredMiddleware:
       def __init__(self, get_response):
           self._get_response = get_response

       def __call__(self, request):
           if request.user.is_authenticated:
               return self._get_response(request)
           login_url = reverse("login")
           if request.path.startswith((login_url, settings.STATIC_URL)):
               return self._get_response(request)
           return redirect(f"{login_url}?next={request.path}")

Place the middleware after :class:`~django.contrib.auth.middleware.AuthenticationMiddleware` so ``request.user`` is populated when the guard runs.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
       "notes.middleware.LoginRequiredMiddleware",
   ]

Wire the Login Route
~~~~~~~~~~~~~~~~~~~~~

Mount Django's :doc:`auth views <django:topics/auth/default>` before the file router include so ``/login/`` resolves before the router claims the path.

.. code-block:: python
   :caption: config/urls.py

   from django.contrib.auth import views as auth_views
   from django.urls import include, path

   urlpatterns = [
       path("login/", auth_views.LoginView.as_view(), name="login"),
       path("logout/", auth_views.LogoutView.as_view(), name="logout"),
       path("", include("next.urls")),
   ]

Read the User in a Page
~~~~~~~~~~~~~~~~~~~~~~~

Past the guard every request carries an authenticated user.
A ``@context`` callable asks for the request by annotation and reads ``request.user``.

.. code-block:: python
   :caption: notes/routes/page.py

   from django.http import HttpRequest
   from next.pages import context

   @context("greeting")
   def greeting(request: HttpRequest) -> str:
       """Return a greeting for the signed-in user."""
       return f"Signed in as {request.user.get_username()}"

Guard One Page Without Middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When only a few pages need protection, skip the middleware and raise :exc:`~django.core.exceptions.PermissionDenied` from the page itself.
Django renders the ``403`` handler for an anonymous request.

.. code-block:: python
   :caption: notes/routes/admin-notes/page.py

   from django.core.exceptions import PermissionDenied
   from django.http import HttpRequest
   from next.pages import context

   @context("notes")
   def notes(request: HttpRequest) -> list:
       """Return staff-only notes or deny access."""
       if not request.user.is_staff:
           raise PermissionDenied
       return list(request.user.note_set.all())

Verification
------------

Start the server and request a protected page while signed out.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver

Visiting ``/notes/`` while anonymous redirects to ``/login/?next=/notes/``.
After signing in the same path renders the page and the greeting names the user.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for reading the request in callables.
   :doc:`/content/howto/integrate-django-admin` for mounting Django views beside the router.
   :doc:`/content/howto/customize-error-pages` for a custom ``403`` template.
