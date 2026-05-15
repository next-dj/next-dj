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

The framework does not bypass any standard Django middleware.

- CSRF tokens flow through the standard ``CsrfViewMiddleware``.
- Session management uses the standard ``SessionMiddleware``.
- Authentication uses ``AuthenticationMiddleware`` and the standard auth backends.
- Permissions checking, password hashing, and signed cookies remain unchanged.
- Django template engine auto escaping is active for every page and component template.

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

SQL injection.
   The Django ORM uses parameterised queries.
   Raw SQL inside a custom provider must use ``params``.

Mass assignment.
   Use ``ModelForm.Meta.fields`` to whitelist editable fields.
   Avoid ``Meta.exclude`` because new fields default to editable.

Path traversal.
   The file router validates page paths against ``BASE_DIR``.
   A submission with a ``_next_form_page`` outside ``BASE_DIR`` returns HTTP 400.

Open redirect.
   ``HttpResponseRedirect`` accepts any URL.
   Validate destinations before passing user input into a redirect target.

Production Hardening
--------------------

A short list of production specific settings.

- ``SECURE_BROWSER_XSS_FILTER = True``.
- ``SECURE_CONTENT_TYPE_NOSNIFF = True``.
- ``SECURE_HSTS_SECONDS = 31536000``.
- ``SECURE_HSTS_INCLUDE_SUBDOMAINS = True``.
- ``SECURE_HSTS_PRELOAD = True``.
- ``SESSION_COOKIE_SECURE = True``.
- ``CSRF_COOKIE_SECURE = True``.
- ``CSRF_TRUSTED_ORIGINS = ["https://..."]``.

Run ``uv run python manage.py check --deploy`` and resolve every warning.

System Checks
-------------

The framework system checks cover configuration mistakes that affect security.

- ``next.E041`` reports an action that handles ``form`` without declaring ``form_class``.
- ``next.E060`` reports an unknown dispatch backend dotted path.
- ``next.E020`` reports a component name collision that could mask a third party component.

Run them with ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`csrf-and-forms` for the form pipeline.
   :doc:`static-assets` for the static pipeline.
   :doc:`di-and-untrusted-input` for the dependency surface.
   :doc:`reporting` for vulnerability disclosure.
