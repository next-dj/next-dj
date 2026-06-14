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

Use :func:`next.urls.page_reverse` instead of a hard-coded path when redirecting to a file-routed login page.
See :doc:`/content/topics/url-reversing` for the full reversing surface.

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
   :caption: notes/pages/page.py

   from django.http import HttpRequest
   from next.pages import context

   @context("greeting")
   def greeting(request: HttpRequest) -> str:
       """Return a greeting for the signed-in user."""
       return f"Signed in as {request.user.get_username()}"

Guard One Page Without Middleware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When only a few pages need protection, skip the middleware and raise :exc:`~django.core.exceptions.PermissionDenied` from the page itself.
Exceptions raised inside a ``@context`` callable propagate to Django's request stack unchanged, so :exc:`~django.core.exceptions.PermissionDenied` and :exc:`~django.http.Http404` reach the resolver.
Only context-processor failures are swallowed.
Django renders the ``403`` handler for an anonymous request.
A branded 403 page needs a ``403.html`` template or a ``handler403`` in the root URLconf, see :doc:`/content/howto/customize-error-pages`.

.. code-block:: python
   :caption: notes/pages/admin-notes/page.py

   from django.core.exceptions import PermissionDenied
   from django.http import HttpRequest
   from next.pages import context

   @context("notes")
   def notes(request: HttpRequest) -> list:
       """Return staff-only notes or deny access."""
       if not request.user.is_staff:
           raise PermissionDenied
       return list(request.user.note_set.all())

Guard Form Actions
~~~~~~~~~~~~~~~~~~

Form actions dispatch at ``/_next/form/<uid>/``, outside the page URL space.
The global middleware above still covers them because it gates every request path.
A project that protects pages selectively declares the requirement on the action itself.
Use ``Meta.login_required`` and ``Meta.permission_required`` on the form class, or the same keywords on ``@action``.

.. code-block:: python
   :caption: notes/pages/admin-notes/page.py

   import next.forms
   from notes.models import Note

   class AdminNoteForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title"]
           login_required = True
           permission_required = "notes.change_note"

An anonymous POST redirects to ``LOGIN_URL`` with ``next`` set to the origin page, and an authenticated user missing the permission gets HTTP 403.
The guard protects the mutation, not the markup: a GET still renders the page and its form, so hide the form in the template when anonymous visitors should not see it.
See :ref:`topics-forms-actions-guards` for the full semantics, including guard inheritance.
For a per-request decision that the static keys cannot express, such as owner-only edits, override ``check_permissions`` or ``has_object_permission`` on the form class, see :ref:`topics-forms-actions-dynamic-guards`.

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
