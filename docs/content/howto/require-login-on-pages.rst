.. _howto-require-login-on-pages:

Require login on file-routed pages
==================================

Problem
-------

Every page under the file router must be visible only to authenticated users, and anonymous visitors should land on the login page.
A few pages, the login page first among them, stay open to everyone.

Solution
--------

List Django's :class:`~django.contrib.auth.middleware.LoginRequiredMiddleware` in ``MIDDLEWARE``.
It closes every view that is not marked public, and the view of a page reads that mark from the page itself.
A page that anyone may visit declares ``page.public()``, and a section that should stay closed whatever the settings say declares ``proxy.login_required()`` in its ``proxy.py``.

Walkthrough
-----------

List the middleware
~~~~~~~~~~~~~~~~~~~

Place the middleware after :class:`~django.contrib.auth.middleware.AuthenticationMiddleware`, so ``request.user`` exists when it runs.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.contrib.auth.middleware.LoginRequiredMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
   ]

   LOGIN_URL = "/login/"

The middleware redirects an anonymous request for any page to ``LOGIN_URL`` with the full path as ``next``.
A zone request travels the URL of its page, so it meets the same redirect, and the client runtime follows it with a full navigation.

The framework marks its own endpoints with :func:`~django.contrib.auth.decorators.login_not_required`, the action endpoint ``/_next/form/<uid>/``, the CSRF endpoint ``/_next/csrf/``, ``sitemap.xml``, and ``robots.txt``.
An action is not left open by that mark, because the dispatcher requires a login for an action unless the rule of the page of its directory is public, through ``page.public()`` or a ``proxy.py`` above it, and its origin is public too.

Open the public pages
~~~~~~~~~~~~~~~~~~~~~

A page that anyone may visit declares ``page.public()`` at module level of its ``page.py``.

.. code-block:: python
   :caption: notes/pages/about/page.py

   from next import page

   page.public()

The view of that page carries ``login_required = False``, the attribute the middleware reads.
The actions of the same ``page.py`` posted from it stay open as well, so a login page built as a page of the tree declares ``page.public()`` and its form keeps working for anonymous visitors.
A ``proxy.py`` that declares ``proxy.public()`` opens its directory and every page below it the same way.
Keep ``LOGIN_URL`` on a public page or on Django's :class:`~django.contrib.auth.views.LoginView`, which Django marks public itself, since a closed login page redirects to itself forever.
``page.public()`` also lifts the requirements of every ``proxy.py`` above the page, and ``manage.py check`` lists such a lift as ``next.W135``, so an opening under a closed section never goes unnoticed, see :ref:`topics-proxy`.

Close a section in code
~~~~~~~~~~~~~~~~~~~~~~~

A ``proxy.py`` closes its directory and every page below it, independent of ``MIDDLEWARE``.

.. code-block:: python
   :caption: notes/pages/notes/proxy.py

   from next.pages import proxy

   proxy.login_required()
   proxy.permission_required("notes.view_note")

Every page under ``notes/``, every action declared there, and every zone request of those pages now requires a logged-in user who holds ``notes.view_note``.
An anonymous visitor is redirected to ``LOGIN_URL``, and a user without the permission gets HTTP 403.
A branded 403 page needs a ``403.html`` template or a ``handler403`` in the root URLconf, see :doc:`/content/howto/customize-error-pages`.
A tree mounted under a prefix of this one inherits no ``proxy.py`` of it, so it declares its own, see :doc:`/content/topics/proxy`.

Wire the login route
~~~~~~~~~~~~~~~~~~~~

Mount Django's :doc:`auth views <django:topics/auth/default>` before the file router include so ``/login/`` resolves before the router claims the path, or route the login through a public page as shown above.

.. code-block:: python
   :caption: config/urls.py

   from django.contrib.auth import views as auth_views
   from django.urls import include, path

   urlpatterns = [
       path("login/", auth_views.LoginView.as_view(), name="login"),
       path("logout/", auth_views.LogoutView.as_view(), name="logout"),
       path("", include("next.urls")),
   ]

Use :func:`~next.urls.reverse.page_reverse` instead of a hard-coded path when ``LOGIN_URL`` names a file-routed page, see :doc:`/content/topics/url-reversing`.

Read the user in a page
~~~~~~~~~~~~~~~~~~~~~~~

Past the middleware every request to a closed page carries an authenticated user.
A ``@context`` callable asks for the request by annotation and reads ``request.user``.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpRequest

   from next import context

   @context("greeting")
   def greeting(request: HttpRequest) -> str:
       return f"Signed in as {request.user.get_username()}"

Actions declared outside the tree
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An action declared in a ``page.py`` or in another file of a page tree answers to the access of the page in its directory, whatever origin the submission names.
A shared form in an ``app/forms.py`` belongs to no tree, so under the middleware it requires a login at any origin, and without the middleware it answers to the page it is posted from, so it declares its own guard when it must stay closed either way.

.. code-block:: python
   :caption: notes/forms.py

   from notes.models import Note

   import next.forms

   class AdminNoteForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title"]
           login_required = True
           permission_required = "notes.change_note"

An anonymous POST redirects to ``LOGIN_URL`` with ``next`` set to the origin page, and an authenticated user missing the permission gets HTTP 403.
See :doc:`/content/security/actions` for the owner rule and :ref:`topics-forms-actions-guards` for the guard semantics.
For owner-only edits, register the rule as a predicate in ``permissions.py`` and name it in ``Meta.permission_required``, see :doc:`/content/howto/enforce-object-level-permissions`.

Verification
------------

List the access of every page, then request a closed page while signed out.

.. code-block:: bash
   :caption: shell

   uv run python manage.py next permissions
   uv run python manage.py runserver

The table shows ``login (middleware)`` for a page the middleware closes, ``public`` for the login page, and the requirements of ``notes/proxy.py`` with that file as their source for every page below it.
Visiting ``/notes/`` while anonymous redirects to ``/login/?next=/notes/``, and after signing in the same path renders the page and the greeting names the user.

See also
--------

.. seealso::

   :ref:`topics-permissions-tree` for the declarations and the order of the check.
   :doc:`/content/topics/dependency-injection` for reading the request in callables.
   :doc:`/content/howto/integrate-django-admin` for mounting Django views beside the router.
   :doc:`/content/howto/customize-error-pages` for a custom ``403`` template.
