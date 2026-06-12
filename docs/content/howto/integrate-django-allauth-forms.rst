.. _howto-allauth:

Integrate django-allauth Forms
==============================

Problem
-------

You want the allauth login, signup, and password-reset forms inside next.dj pages, rendered through ``{% form %}`` and dispatched through form actions instead of the allauth views.

Solution
--------

Register each allauth form through an action whose ``form_class`` is a factory returning ``(FormClass, init_kwargs)``, and let the handler return the allauth flow response as is.
For the next-forms style, a thin subclass with ``on_valid`` works too, with one mandatory line for request-aware forms.
No bridge code is involved.
Verified with django-allauth 65.x on Django 5.2 and 6.0.

Walkthrough
-----------

Configure allauth
~~~~~~~~~~~~~~~~~

The standard allauth setup applies in full, even when every form lives in a next.dj page.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       # ...
       "allauth",
       "allauth.account",
   ]

   MIDDLEWARE = [
       # ...
       "allauth.account.middleware.AccountMiddleware",
   ]

   AUTHENTICATION_BACKENDS = [
       "django.contrib.auth.backends.ModelBackend",
       "allauth.account.auth_backends.AuthenticationBackend",
   ]

   ACCOUNT_LOGIN_METHODS = {"username"}
   ACCOUNT_SIGNUP_FIELDS = ["username*", "email*", "password1*", "password2*"]
   LOGIN_REDIRECT_URL = "/welcome/"

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("accounts/", include("allauth.urls")),
       path("", include("next.urls")),
   ]

``AccountMiddleware`` is mandatory.
Beyond the allauth system checks, it publishes the current request through ``allauth.core.context``, which the adapter rate limiter reads during validation.
``allauth.urls`` must stay mounted even when no allauth view is linked directly.
The reset email reverses ``account_reset_password_from_key``, and mandatory email verification redirects to ``/accounts/confirm-email/``, so both flows break without the URLconf entry.

Login Through a Factory
~~~~~~~~~~~~~~~~~~~~~~~

The recommended pattern is a factory plus a handler.
The factory passes the request into the allauth constructor, and the handler hands the validated form back to the allauth flow.

.. code-block:: python
   :caption: accounts/pages/login/page.py

   from allauth.account.forms import LoginForm
   from django.http import HttpRequest, HttpResponse

   from next.forms import action

   def login_form_factory(request: HttpRequest) -> tuple[type[LoginForm], dict[str, HttpRequest]]:
       return LoginForm, {"request": request}

   @action("login", form_class=login_form_factory)
   def login(request: HttpRequest, form: LoginForm) -> HttpResponse:
       return form.login(request, redirect_url=None)

.. code-block:: jinja
   :caption: accounts/pages/login/template.djx

   {% form "login" %}
     {{ form.as_div }}
     <button type="submit">Sign in</button>
   {% endform %}

The ``init_kwargs`` reach both the POST constructor and the GET render, so the dynamic ``login`` field that allauth builds in ``__init__`` appears in the initial render and in the error re-render.
Wrong credentials answer with HTTP 200, ``X-Next-Form: invalid``, the allauth error message, and the entered login preserved.
Valid credentials answer with the allauth redirect to ``LOGIN_REDIRECT_URL`` and an authenticated session.

Signup and the ``{"initial": {}}`` Idiom
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``SignupForm`` takes no ``request`` kwarg, but the factory must still return non-empty ``init_kwargs``.
A factory that returns a bare class or an empty dict routes the dispatcher into its ``get_initial`` branch, and an allauth form has no ``get_initial``, so the request fails with a ``TypeError``.
The neutral ``{"initial": {}}`` keeps construction on the factory path, the same idiom :doc:`use-formsets` documents for formsets.

.. code-block:: python
   :caption: accounts/pages/signup/page.py

   from allauth.account import app_settings as account_settings
   from allauth.account.forms import SignupForm
   from allauth.account.utils import complete_signup
   from django.http import HttpRequest, HttpResponse

   from next.forms import action

   def signup_form_factory() -> tuple[type[SignupForm], dict[str, dict[str, object]]]:
       return SignupForm, {"initial": {}}

   @action("signup", form_class=signup_form_factory)
   def signup(request: HttpRequest, form: SignupForm) -> HttpResponse:
       user, response = form.try_save(request)
       if response is not None:
           return response
       return complete_signup(request, user, account_settings.EMAIL_VERIFICATION, None)

``try_save`` returns an early response when allauth intervenes, for example the enumeration-prevention flow, and the handler passes it through unchanged.

Password Reset
~~~~~~~~~~~~~~

``ResetPasswordForm.save`` sends the email and returns the address string rather than a response, so the handler answers with its own redirect.

.. code-block:: python
   :caption: accounts/pages/password-reset/page.py

   from allauth.account.forms import ResetPasswordForm
   from django.http import HttpRequest, HttpResponseRedirect

   from next.forms import action

   def reset_form_factory() -> tuple[type[ResetPasswordForm], dict[str, dict[str, object]]]:
       return ResetPasswordForm, {"initial": {}}

   @action("password_reset", form_class=reset_form_factory)
   def password_reset(request: HttpRequest, form: ResetPasswordForm) -> HttpResponseRedirect:
       form.save(request)
       return HttpResponseRedirect("/password-reset/sent/")

Alternative: a Thin Subclass
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A subclass keeps the next-forms style with auto-registration and ``on_valid``.
Both metaclasses are ``DeclarativeFieldsMetaclass``, so the bases compose.

.. code-block:: python
   :caption: accounts/forms.py — auto-registered as ``login_action``

   from allauth.account.forms import LoginForm
   from allauth.core import context as allauth_context
   from django.http import HttpRequest, HttpResponse

   from next.forms import Form

   class LoginAction(Form, LoginForm):
       def __init__(
           self,
           *args: object,
           request: HttpRequest | None = None,
           **kwargs: object,
       ) -> None:
           if request is None:
               request = allauth_context.request
           super().__init__(*args, request=request, **kwargs)

       def on_valid(self, request: HttpRequest) -> HttpResponse:
           return self.login(request, redirect_url=None)

The request recovery in ``__init__`` is mandatory for request-aware forms such as ``LoginForm``.
The dispatcher builds a registered form from the POST data without a ``request`` kwarg, so the allauth constructor stores ``self.request = None``, and ``clean()`` then crashes inside the login rate limiter, which calls ``get_current_site(self.request)``.
The GET render works without the line, the crash fires only on submit.
``AccountMiddleware`` publishes the live request through ``allauth.core.context``, and the constructor falls back to it.

.. warning::

   Do not wire allauth through a handler-only action that instantiates the form itself.
   When the form fails validation the handler has nothing to return, and ``None`` from a handler-only action answers with ``204 No Content``.
   No origin re-render, no ``X-Next-Form`` header, and no visible errors.
   Register the form through ``form_class`` so the dispatcher owns the invalid path.

Sharp Edges
~~~~~~~~~~~

Rate limits need a cache.
   The default ``ACCOUNT_RATE_LIMITS`` are active and work through the Django cache, so ``CACHES`` must be configured.
   The local-memory backend is enough for development.

Enumeration prevention changes the invalid path.
   ``ACCOUNT_PREVENT_ENUMERATION`` defaults to ``True``, so a reset submission with an unknown address validates and answers as if the email was sent.
   Build the invalid-path tests on a malformed email, not on an unknown one.

MFA redirects should pass through.
   A two-factor stage is a redirect response from ``form.login``, and the dispatcher passes handler responses through unchanged, so the flow should work as is.
   This path is not verified.

Verification
------------

.. code-block:: python
   :caption: tests/test_login.py

   from next.testing.client import NextClient

   def test_wrong_password_rerenders(db, user) -> None:
       client = NextClient()
       resp = client.post_action(
           "login", {"login": "ada", "password": "wrong"}, origin="/login/"
       )
       assert resp.status_code == 200
       assert resp["X-Next-Form"] == "invalid"
       assert "not correct" in resp.content.decode()

   def test_valid_login_authenticates(db, user) -> None:
       client = NextClient()
       resp = client.post_action(
           "login",
           {"login": "ada", "password": "correct-horse-staple"},
           origin="/login/",
       )
       assert resp.status_code == 302
       assert resp["Location"] == "/welcome/"
       assert client.session.get("_auth_user_id") == str(user.pk)

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/actions` for the factory and handler contracts.
   :doc:`/content/topics/forms/validation-rerender` for the invalid-path behaviour.
   :doc:`use-formsets` for the non-empty ``init_kwargs`` idiom.
