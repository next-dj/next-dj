.. _security-overview:

Security Overview
=================

next.dj relies on Django's middleware stack and template engine for the bulk of its security guarantees.
This page lists the Django mechanisms that apply unchanged and the framework specific surfaces that need extra attention.

.. contents::
   :local:
   :depth: 2

Django Guarantees Used Unchanged
--------------------------------

The framework does not bypass any standard :doc:`Django middleware <django:topics/http/middleware>`.

- CSRF tokens flow through the standard :doc:`CsrfViewMiddleware <django:ref/csrf>`.
- Session management uses the standard :doc:`SessionMiddleware <django:topics/http/sessions>`.
- Authentication uses ``AuthenticationMiddleware`` and the standard :doc:`auth backends <django:topics/auth/index>`.
- Permissions checking, password hashing, and signed cookies remain unchanged.
- Django template engine :doc:`auto escaping <django:ref/templates/language>` is active for every page and component template.

A standard ``MIDDLEWARE`` block in ``settings.py`` is therefore enough to inherit the full Django security baseline.

Framework Specific Surfaces
---------------------------

The framework adds three surfaces that warrant attention.

File router input.
   Captured URL parameters and query values reach Python through the dependency resolver.
   Treat them as untrusted, see :doc:`di-and-untrusted-input`.

Form dispatch path.
   ``/_next/form/<uid>/`` is the dispatch endpoint for every action.
   See :doc:`csrf-and-forms` for the CSRF flow.

Co-located assets.
   Component and page level CSS and JS ship through the static collector.
   See :doc:`static-assets` for origins, hashes, and integrity.

Common Threats
--------------

CSRF.
   Django middleware plus the framework ``{% form %}`` tag covers the standard form path.
   Manual ``<form>`` elements need explicit ``{% csrf_token %}``.

XSS.
   Django template auto escaping prevents most cases.
   Context functions that return ``mark_safe`` strings or HTML strings bypass escaping.
   Apply ``mark_safe`` only to values you fully control, and never to untrusted input as covered in :doc:`di-and-untrusted-input`.

SQL injection.
   The :doc:`Django ORM <django:topics/db/queries>` uses parameterised queries.
   Raw SQL inside a custom provider must use ``params``.
   See :doc:`di-and-untrusted-input` for the custom-provider validation pattern.

Mass assignment.
   Whitelist editable fields on ``ModelForm``, see :doc:`di-and-untrusted-input` for the rule.

Origin spoofing.
   The only page identity a form submission carries is the ``_next_form_origin`` URL path, which the dispatcher resolves through the URLconf with :func:`django.urls.resolve`.
   The client never supplies a filesystem path, so an error re-render can target only pages that are reachable through the routing table anyway.
   A value that does not resolve returns HTTP 400.
   Substituting the origin of another routed page remains possible and is an authorization question, so guard mutating actions as described under `Access Control`_.

Open redirect.
   ``HttpResponseRedirect`` accepts any URL.
   Validate destinations before passing user input into a redirect target.

Object-level authorization.
   A lookup keyed only on a URL value loads whatever row matches, regardless of who owns it.
   The ModelForm ``Meta.instance_from_url`` lookup is unscoped, so scope it to the user or tenant.
   See :doc:`/content/topics/forms/modelforms` for the ownership-scoped pattern and :doc:`di-and-untrusted-input` for the posted origin path that feeds the lookup.

Access Control
--------------

Form actions are unauthenticated by default.
The ``/_next/form/<uid>/`` endpoint accepts a POST from any visitor, so a registered edit or delete action runs without an identity check unless the handler adds one.

Enforce access at one of these layers.

- Declare ``Meta.login_required`` and ``Meta.permission_required`` on the form class, or the same keywords on ``@action``, for a static guard checked before any application code, see :ref:`topics-forms-actions-guards`.
- Override ``check_permissions`` or ``has_object_permission`` on the form class for a per-request decision against the database, the tenant, or the loaded row, see :ref:`topics-forms-actions-dynamic-guards`.
- Check ``request.user.is_authenticated`` and ownership inside ``on_valid`` before ``self.save()``.
- Apply a project-wide login requirement through middleware, see :doc:`/content/howto/require-login-on-pages`.
- Enforce a policy in a custom form action backend that wraps every dispatch.

An action that mutates data and an action that loads an instance through ``instance_from_url`` both need this guard.
The :ref:`howto-enforce-object-level-permissions` recipe shows the owner-only edit on a ``ModelForm``.

Production Hardening
--------------------

A short list of production specific settings.

- Set ``SECURE_SSL_REDIRECT = True`` to redirect every HTTP request to HTTPS.
- Set ``SECURE_CONTENT_TYPE_NOSNIFF = True`` to block MIME-type sniffing.
- Set ``SECURE_HSTS_SECONDS = 31536000`` to send a one-year HSTS header.
- Set ``SECURE_HSTS_INCLUDE_SUBDOMAINS = True`` to extend HSTS to all subdomains.
- Set ``SECURE_HSTS_PRELOAD = True`` to allow submission to the HSTS preload list.
- Set ``SESSION_COOKIE_SECURE = True`` to send the session cookie only over HTTPS.
- Set ``CSRF_COOKIE_SECURE = True`` to send the CSRF cookie only over HTTPS.
- Set ``CSRF_TRUSTED_ORIGINS = ["https://..."]`` to restrict cross-origin form submissions to listed origins.

Run ``uv run python manage.py check --deploy`` and resolve every warning.
See :doc:`/content/deployment/checklist` for the full pre-deploy review.

System Checks
-------------

The framework system checks cover configuration mistakes that affect security.

- ``next.E041`` reports two actions registered under the same name from different handlers.
- ``next.E045`` reports a form action backend that does not subclass ``FormActionBackend``.
- ``next.E020`` reports a component name collision that could mask a third party component.

Run them with ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`csrf-and-forms` for the form pipeline.
   :doc:`static-assets` for the static pipeline.
   :doc:`di-and-untrusted-input` for the dependency surface.
   :doc:`/content/topics/static-assets/js-context` for runtime script options that interact with CSP.
   :doc:`reporting` for vulnerability disclosure.
