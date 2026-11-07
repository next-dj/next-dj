.. _security-sessions-and-auth:

Sessions and authentication
===========================

This page states what next.dj does with the session and the authenticated user, which is far less than a reader expects, and where the two nevertheless touch framework code.

.. contents::
   :local:
   :depth: 2

What the framework does not touch
---------------------------------

The framework ships no authentication backend, no user model, no login view, and no session engine.
``request.user`` arrives from Django's ``AuthenticationMiddleware`` and ``request.session`` from ``SessionMiddleware``, exactly as in a project with no next.dj in it.
The dependency resolver reads both off the request and never constructs either, so a page that annotates ``request: HttpRequest`` sees the same object a plain Django view sees.

Session rotation follows from that.
The framework never calls :func:`django.contrib.auth.login` or :func:`django.contrib.auth.logout`, so the key cycling those two perform against session fixation stays in project code that calls them.
A login rendered through a form action is an ordinary handler calling the ordinary helper.

The framework reads the user itself in two places, the access rule of the page tree and the action guard.
A ``login_required()`` or ``permission_required()`` that a ``page.py`` declares tests ``request.user.is_authenticated`` and ``user.has_perm`` before the page, its actions, and its zones run, and it redirects an anonymous visitor to ``LOGIN_URL`` with the full path as ``next``, see :ref:`topics-permissions-tree`.
The same declaration in a ``component.py`` hides the component from a refused user, which renders it as an empty string with no redirect.
``NEXT_FRAMEWORK["LOGIN_REQUIRED"]`` or a listed ``LoginRequiredMiddleware`` asks the same login of every page, action, and zone that declares nothing, see :ref:`topics-access-default-login`.
``Meta.login_required`` and ``Meta.permission_required`` test ``request.user.is_authenticated`` and ``user.has_perms`` before any POST data reaches a handler, and an anonymous caller gets a 302 to ``LOGIN_URL`` carrying the validated origin path as ``next``, see :ref:`topics-forms-actions-guards`.
``manage.py check`` reports ``next.W060`` when an action declares ``permission_required`` while ``django.contrib.auth`` is out of ``INSTALLED_APPS``, because the guard cannot resolve a permission without it.

Beyond those checks the framework runs project code that reads the user on its behalf.
A submission is authorized by the page its origin names and by the page that owns the action, which means the ``render()`` of each runs on the POST and whatever identity check it performs decides the answer, see :doc:`/content/topics/pages`.
The dynamic ``check_permissions`` and ``has_object_permission`` hooks read the user the same way, from application code the dispatcher calls.
A permission registered in a ``permissions.py`` runs its predicate on the user and the bound row, through ``ObjectPermissionBackend`` in ``AUTHENTICATION_BACKENDS``, see :doc:`/content/topics/permissions`.

Intercepting routes
-------------------

An intercepting page serves a zone of its target page, and a zone request over the target runs both access chains, the stricter winning.
A denial of the intercept is the response, with no fallback to the target.
The form and action path answers a denied intercept with 403 and no redirect.
An action guard protects a mutation, and the modal needs a rule of its own on the intercept ``page.py``.
Zone names are public markup, so the rule is the protection and the name is not.
Under the default login an intercept that declares nothing requires a login even over a public target, and ``page.public()`` on the intercept opens it, see :ref:`topics-access-intercepts`.

Where the framework writes to the session
-----------------------------------------

One subsystem writes, the multi-step ``FormWizard``.
Each step's ``cleaned_data`` is persisted between requests so the flow can span several POSTs, and the store is a ``FormWizardBackend`` chosen through ``FORM_WIZARD_BACKEND``.

The bundled ``SessionFormWizardBackend`` writes one ``_next_wizard:<storage_id>`` key per wizard into ``request.session``, where the storage id hashes the wizard's scope key together with its instance id.
The bundled ``CacheFormWizardBackend`` writes to the Django cache under ``next_wizard:<session_key>:<storage_id>`` instead and forces a session to exist so the key has something to hang on, which means it depends on the session even though the bytes live elsewhere.
Both need ``django.contrib.sessions`` installed, and ``manage.py check`` reports ``next.W056`` when a wizard is registered without it.

Two consequences follow for a wizard that collects anything sensitive.
Partially entered data sits in the store from the first step until the flow finishes, so it outlives the request that entered it and inherits the retention of whatever store holds it, the session cookie or table for one backend and the cache timeout for the other.
The dispatcher clears the store as soon as ``done`` returns a successful response, and a flow the visitor abandons is cleared by session or cache expiry alone, so set ``SESSION_COOKIE_AGE`` or the cache ``TIMEOUT`` to a window the data deserves.

A cookie-backed session engine is the sharpest edge here, because a signed cookie session carries every step's cleaned data to the browser and back on every request.
Use a server-side engine for a wizard that gathers data the visitor should not hold, see :doc:`/content/topics/forms/wizard-backend` for the codec rules each backend applies.

Where the CSRF token ends up in HTML
------------------------------------

The token reaches the page twice.
Every ``{% form %}`` block emits the standard ``<input type="hidden" name="csrfmiddlewaretoken">``, and the page's inline ``Next._init`` payload carries a ``$csrf`` entry holding the header name and the token, which is how the client runtime signs an unsafe request without touching the cookie.

Both land in the document, so any script running on the page can read the token.
``CSRF_COOKIE_HTTPONLY`` therefore protects the cookie against theft by something outside the page, and it does not put the token out of reach of page scripts, see :doc:`csrf-and-forms` for the full cookie discussion.
A page under XSS has already lost the token whatever that flag says, which is the reason the XSS entry in :doc:`overview` ranks above this one.

Settings a deployment sets
--------------------------

Every one of these is a stock Django setting, and ``manage.py check --deploy`` reports the unsafe values.

- ``SESSION_COOKIE_SECURE = True`` sends the session cookie over HTTPS only.
- ``SESSION_COOKIE_HTTPONLY = True`` keeps the session cookie out of reach of page scripts, and it is the Django default.
- ``SESSION_COOKIE_SAMESITE`` decides whether a cross-site navigation carries the session, with ``"Lax"`` as the Django default and ``"Strict"`` as the tighter option.
- ``SESSION_COOKIE_AGE`` and ``SESSION_EXPIRE_AT_BROWSER_CLOSE`` bound how long a session and any wizard draft inside it survive.
- ``SESSION_ENGINE`` selects where the session lives, and a wizard argues for a server-side engine as described above.
- ``LOGIN_URL`` is where a guarded page or action redirects an anonymous caller, so it points at a real view, and under the default login that view is public.

See Django's :doc:`session documentation <django:topics/http/sessions>` for the engines and :doc:`its deployment checklist <django:howto/deployment/checklist>` for the values a production run expects.

See also
--------

.. seealso::

   :doc:`csrf-and-forms` for the token flow and the cookie flags.
   :doc:`/content/topics/forms/wizard-backend` for the draft-persistence contract.
   :doc:`/content/howto/require-login-on-pages` for a project-wide login requirement.
   :doc:`actions` for the rule a form action answers to.
   :doc:`overview` for the broader security picture.
