.. _security-csrf-forms:

CSRF and Forms
==============

This page covers how CSRF protection works through the next.dj dispatch path and what to do for forms that bypass the standard ``{% form %}`` tag.

.. contents::
   :local:
   :depth: 2

Standard Path
-------------

The ``{% form %}`` tag emits a CSRF token automatically.
A bound page renders ``<input type="hidden" name="csrfmiddlewaretoken" value="...">`` inside the form element.

Django's :doc:`CsrfViewMiddleware <django:ref/csrf>` validates the token on every POST to ``/_next/form/<uid>/``.
A missing or stale token returns HTTP 403.

The tag also depends on ``request`` existing in the template context so Django can render the CSRF field.
If ``manage.py check`` reports a missing ``request`` context processor, add ``django.template.context_processors.request`` to the ``OPTIONS.context_processors`` list of your Django ``TEMPLATES`` entry.
An equivalent processor that supplies ``request`` works as well, so layouts receive ``request``.

Origin Validation
-----------------

The framework adds a second hidden field named ``_next_form_origin``.
The ``{% form %}`` tag sets it to the URL path the form was rendered under, so the HTML never exposes the server filesystem layout.
The dispatcher resolves the field only when it has to re-render the origin page: on a validation failure, and when a form-backed handler returns ``None`` so the page renders again.
A valid submission whose handler returns a response never resolves the field, so the check is a re-render lookup, not a pre-handler origin gate.

When the field is resolved, these checks apply.

The value must be a same-site path that starts with ``/`` and not with ``//``.
It must resolve against the URLconf through :func:`django.urls.resolve`, and the resolved view must carry the ``next_page_path`` attribute the file router sets on every routed page.
The client therefore never names a file, and a re-render can target only pages that are already reachable through the routing table.
A re-render whose field fails these checks returns HTTP 400.

On the success path the same field feeds ``redirect_to_origin`` without URLconf resolution.
A missing or off-site value never blocks a successful dispatch, ``redirect_to_origin`` simply falls back to ``/``.
Handlers can call ``redirect_to_origin`` from ``next.forms`` to redirect back to the page that rendered the form.

Manual Forms
------------

The ``{% form %}`` tag is the supported way to render a form.
It builds the dispatch URL, injects the CSRF token, and emits the hidden ``_next_form_origin`` field.
A hand crafted ``<form>`` element bypasses these guarantees, so prefer the tag.

When a hand crafted form is unavoidable, render the tag once and copy the generated markup, or keep the form inside a ``{% form %}`` block and add only the extra fields you need.

A fully manual form sets ``_next_form_origin`` to ``{{ request.path }}``, the same value the tag emits.
A form rendered by a hand-written view outside the file router additionally needs the ``next_page_path`` attribute on that view before the error re-render works, see :ref:`topics-forms-templates-handwritten-views`.

GET Forms
---------

GET forms do not need CSRF protection.
Use a plain ``<form method="get">`` for search inputs and filtering panels.

.. code-block:: jinja
   :caption: search form

   <form method="get" action="{% url 'next:page_search' %}">
     <input type="text" name="q" value="{{ query }}">
     <button type="submit">Search</button>
   </form>

Use ``DQuery[str]`` in the page context to read the value.

.. note::

   The dispatch endpoint accepts only POST.
   A GET to ``/_next/form/<uid>/`` returns HTTP 405 for a registered action and HTTP 404 for an unknown one, with no CSRF check on the GET.
   The two status codes let the action surface be probed without a token.
   This is intended, the 405 reveals only that a uid is registered and no handler runs on a GET, so do not place secrets in action uids.

AJAX Submissions
----------------

JavaScript that posts to the dispatch URL must supply the CSRF token, in the ``X-CSRFToken`` header or the ``csrfmiddlewaretoken`` body field, and the ``_next_form_origin`` value in the request body.
The standard Django approach reads the token from the cookie or from a meta tag.

The simplest way to obtain the origin is to read the hidden ``_next_form_origin`` field that the rendered ``{% form %}`` tag already emits.

.. code-block:: javascript
   :caption: fetch wrapper

   const cookie = document.cookie
     .split("; ")
     .find((row) => row.startsWith("csrftoken="));
   const token = cookie ? cookie.split("=")[1] : "";

   const formElement = document.querySelector("form");
   const origin = formElement.elements._next_form_origin.value;

   fetch(formElement.action, {
     method: "POST",
     headers: {"X-CSRFToken": token},
     body: new URLSearchParams({
       _next_form_origin: origin,
       title: "From JS",
     }),
   });

This works because every ``{% form %}`` block emits the ``_next_form_origin`` hidden field.

To post without a rendered form, use ``window.location.pathname``.
The origin is the URL path of the current page, so no server-published value is needed.

Wizard Steps
------------

A wizard step POST is stricter than a plain form POST about the origin field.
For a plain form a missing or unresolvable ``_next_form_origin`` blocks only the error re-render, a valid submission still runs and ``redirect_to_origin`` falls back to ``/``.
For a wizard step the dispatcher requires a resolvable origin before any validation runs, because the current step and the next-step redirect both derive from the origin path, and returns HTTP 400 when it is missing or invalid.

The ``{% form %}`` tag emits the field on every render, so a tag-rendered wizard needs nothing extra.
A hand-crafted wizard POST or an AJAX wizard submission must include the ``_next_form_origin`` field with the current page path.

Cross Origin Requests
---------------------

Add the public origin to ``CSRF_TRUSTED_ORIGINS`` for cross subdomain or cross origin submissions.

.. code-block:: python
   :caption: config/settings.py

   CSRF_TRUSTED_ORIGINS = [
       "https://app.example.com",
       "https://admin.example.com",
   ]

The framework does not relax CSRF policy.
The trusted origins list comes directly from Django.

Cookie Settings
---------------

Use these cookie flags in production.

.. code-block:: python
   :caption: config/settings.py

   CSRF_COOKIE_SECURE = True
   CSRF_COOKIE_HTTPONLY = False
   CSRF_COOKIE_SAMESITE = "Lax"

Keep ``CSRF_COOKIE_HTTPONLY`` false so that AJAX requests can read the token.

Common Pitfalls
---------------

Form post without ``_next_form_origin``.
   A validation failure or a handler ``None`` return cannot re-render the origin page and returns HTTP 400.
   Always use ``{% form %}`` or include the field manually.

Language switch between render and submit.
   Under :func:`django.conf.urls.i18n.i18n_patterns` the origin resolves under the language active on the POST.
   A user who changes the language in between posts an origin whose prefix no longer resolves, so a failing validation answers HTTP 400 instead of re-rendering.
   The success path is unaffected because it never resolves the origin.

Stale token after deploy.
   Cached page renders carry the previous token.
   Set short cache lifetimes on HTML or warm the cache after a deploy.

Different origin without ``CSRF_TRUSTED_ORIGINS``.
   The middleware returns 403.
   Add every origin that posts to the project.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/templates` for the form tag.
   :doc:`/content/topics/forms/validation-rerender` for the re-render flow.
   :doc:`overview` for the broader security picture.
