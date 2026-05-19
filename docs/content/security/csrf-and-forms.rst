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
If ``manage.py check`` reports ``next.E019``, add ``django.template.context_processors.request`` to the ``OPTIONS.context_processors`` list of your Django ``TEMPLATES`` entry.
An equivalent processor that supplies ``request`` works as well, so layouts receive ``request``.

Origin Validation
-----------------

The framework adds a second hidden field named ``_next_form_page`` with the absolute path to the rendering page.
The dispatcher validates the path before invoking the handler.

Several checks apply.

The posted path must resolve under ``settings.BASE_DIR``.
It must name ``page.py`` (the framework stores the absolute path to that module).
Either the file must exist, or the directory must contain a sibling ``template.djx`` so virtual routes can re-render after validation failures.

The optional hidden field ``_next_form_origin`` carries a same-site path (starts with ``/``, not ``//``).
Handlers can call ``redirect_to_origin`` from ``next.forms`` to redirect back to the page that rendered the form.

A submission that fails these checks returns HTTP 400 and the handler does not run.

Manual Forms
------------

The ``{% form %}`` tag is the supported way to render a form.
It builds the dispatch URL, injects the CSRF token, and emits the hidden ``_next_form_page`` field.
A hand crafted ``<form>`` element bypasses these guarantees, so prefer the tag.

When a hand crafted form is unavoidable, render the tag once and copy the generated markup, or keep the form inside a ``{% form %}`` block and add only the extra fields you need.

The ``current_page_module_path`` variable is published by the framework on every rendered page.

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

AJAX Submissions
----------------

JavaScript code that posts to the dispatch URL must include both fields in the request body.
The standard Django approach reads the token from the cookie or from a meta tag.

The simplest way to obtain the origin path is to read the hidden ``_next_form_page`` field that the rendered ``{% form %}`` tag already emits.

.. code-block:: javascript
   :caption: fetch wrapper

   const cookie = document.cookie
     .split("; ")
     .find((row) => row.startsWith("csrftoken="));
   const token = cookie ? cookie.split("=")[1] : "";

   const formElement = document.querySelector("form");
   const originPath = formElement.elements._next_form_page.value;

   fetch(formElement.action, {
     method: "POST",
     headers: {"X-CSRFToken": token},
     body: new URLSearchParams({
       _next_form_page: originPath,
       title: "From JS",
     }),
   });

This works because every ``{% form %}`` block emits the ``_next_form_page`` hidden field.

To post without a rendered form, re-publish the existing ``current_page_module_path`` value with ``@context("current_page_module_path", serialize=True)`` so it reaches ``window.Next.context``.

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

Form post without ``_next_form_page``.
   The dispatcher returns HTTP 400.
   Always use ``{% form %}`` or include the field manually.

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
