.. _security-csrf-and-forms:

CSRF and forms
==============

This page covers how CSRF protection works through the next.dj dispatch path and what to do for forms that bypass the standard ``{% form %}`` tag.

.. contents::
   :local:
   :depth: 2

Standard path
-------------

The ``{% form %}`` tag emits a CSRF token automatically.
A bound page renders ``<input type="hidden" name="csrfmiddlewaretoken" value="...">`` inside the form element.

Django's :doc:`CsrfViewMiddleware <django:ref/csrf>` validates the token on every POST to ``/_next/form/<uid>/``.
A missing or stale token returns HTTP 403.

The tag also depends on ``request`` existing in the template context so Django can render the CSRF field.
If ``manage.py check`` reports a missing ``request`` context processor, add ``django.template.context_processors.request`` to the ``OPTIONS.context_processors`` list of your Django ``TEMPLATES`` entry.
An equivalent processor that supplies ``request`` works as well, so layouts receive ``request``.

Origin validation
-----------------

The framework adds a second hidden field named ``_next_form_origin``.
The ``{% form %}`` tag sets it to the URL the form was rendered under with its query string, so the HTML never exposes the server filesystem layout.
The dispatcher resolves the field on every POST, to recover the origin page's URL kwargs for dependency injection and to ask the page it names whether it authorizes the submission.
The checks below therefore run on every POST, and a resolved origin page that carries a ``render()`` gates every action posted from it, see :doc:`/content/topics/pages`.
A value that fails the checks yields no origin match, which blocks nothing by itself, though the paths that need the origin page then answer HTTP 400, a validation failure and a form-backed handler that returns ``None``.

The value passes the same-site test only when all of the following hold.

- The posted value is a string of at most 16384 characters, counted before the strip, so an oversized value is refused without a scan.
- Its surrounding whitespace is stripped before any other test runs.
- It starts with a single ``/`` followed by neither a second slash nor a backslash, so ``//evil.example`` and ``/\evil.example`` are refused as protocol-relative.
  The rule is path-only and reads no host or scheme, so even an absolute URL naming this host is refused.
- It contains no tab, no line feed, and no carriage return, because a browser drops those three code points before it resolves a URL and a value the check read as same-site would become a jump off site.

The value must then resolve against the URLconf through :func:`django.urls.resolve`, its path decoded the way Django builds ``request.path`` and refused again when decoding turns it protocol-relative, and the resolved view must carry the ``next_page_path`` attribute the file router sets on every routed page.
The client therefore never names a file, and a re-render can target only pages that are already reachable through the routing table.
A re-render whose field fails these checks returns HTTP 400.

On the success path the same field feeds ``redirect_to_origin`` without URLconf resolution.
A missing or off-site value never blocks a successful dispatch, ``redirect_to_origin`` falls back to ``/``, and so does an origin longer than Django allows in a redirect ``Location``.
Handlers can call ``redirect_to_origin`` from ``next.forms`` to redirect back to the page that rendered the form.

Manual forms
------------

The ``{% form %}`` tag is the supported way to render a form.
It builds the dispatch URL, injects the CSRF token, and emits the hidden ``_next_form_origin`` field.
A hand crafted ``<form>`` element bypasses these guarantees, so prefer the tag.

When a hand crafted form is unavoidable, render the tag once and copy the generated markup, or keep the form inside a ``{% form %}`` block and add only the extra fields you need.

A fully manual form sets ``_next_form_origin`` to the URL the page was rendered under with its query string, which ``{{ request.get_full_path }}`` produces, the same value the tag emits.
A form rendered by a hand-written view outside the file router additionally needs the ``next_page_path`` attribute on that view before the error re-render works, see :ref:`topics-forms-templates-handwritten-views`.

GET forms
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

AJAX submissions
----------------

JavaScript that posts to the dispatch URL must supply the CSRF token, in the ``X-CSRFToken`` header or the ``csrfmiddlewaretoken`` body field, and the ``_next_form_origin`` value in the request body.
Hand-written code takes the standard Django approach and reads the token from the cookie or from a meta tag.
The bundled client runtime needs neither, because it takes its token from the ``$csrf`` init payload the page emits and never touches the cookie.

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

To post without a rendered form, use ``window.location.pathname + window.location.search``.
The origin is the path of the current page, so no server-published value is needed.
The query string rides along with it, so a redirect back to the origin returns to the same filters and the same page of a listing rather than to its unfiltered first page.

Wizard steps
------------

A wizard step POST is stricter than a plain form POST about the origin field.
For a plain form a missing or unresolvable ``_next_form_origin`` blocks only the error re-render, a valid submission still runs and ``redirect_to_origin`` falls back to ``/``.
For a wizard step the dispatcher requires a resolvable origin before any validation runs, because the current step and the next-step redirect both derive from the origin path, and returns HTTP 400 when it is missing or invalid.

The ``{% form %}`` tag emits the field on every render, so a tag-rendered wizard needs nothing extra.
A hand-crafted wizard POST or an AJAX wizard submission must include the ``_next_form_origin`` field with the current page path.

Cross origin requests
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

Cookie settings
---------------

Use these cookie flags in production.

.. code-block:: python
   :caption: config/settings.py

   CSRF_COOKIE_SECURE = True
   CSRF_COOKIE_HTTPONLY = True
   CSRF_COOKIE_SAMESITE = "Lax"

``CSRF_COOKIE_HTTPONLY`` keeps the cookie itself out of reach of page scripts, which narrows the ways a token is stolen from the browser, and it does not put the token out of reach of a script on the page.
Every ``{% form %}`` block renders the token into a hidden input and the page's inline ``Next._init`` payload carries it under ``$csrf``, so any script running on the page reads it from the document whatever the flag says.
The value of the flag is that an attacker who can read cookies through another channel still does not get this one.

The bundled runtime takes its token from that ``$csrf`` payload rather than from the cookie, so a project that leaves unsafe requests to the runtime keeps the flag on with nothing to change.

.. code-block:: python
   :caption: config/settings.py, when project JavaScript reads the cookie directly

   CSRF_COOKIE_HTTPONLY = False

Set ``CSRF_COOKIE_HTTPONLY`` to false only when project JavaScript reads the token out of the cookie itself, as the fetch wrapper above does.
Reading the hidden input or the init payload instead keeps the flag on, see :doc:`sessions-and-auth`.

Common pitfalls
---------------

Form post without ``_next_form_origin``.
   A validation failure or a handler ``None`` return cannot re-render the origin page and returns HTTP 400.
   Always use ``{% form %}`` or include the field manually.

Language switch between render and submit.
   Under :func:`django.conf.urls.i18n.i18n_patterns` the origin resolves under the language active on the POST.
   A user who changes the language in between posts an origin whose prefix no longer resolves, so a failing validation answers HTTP 400 instead of re-rendering.
   The success path is unaffected, because an origin that does not resolve names no page to re-render and none to authorize.

Stale token after deploy.
   Cached page renders carry the previous token.
   Set short cache lifetimes on HTML or warm the cache after a deploy.

Token rotation on a partial response.
   A login inside a partial flow rotates the CSRF token, and Django flags the rotation on the request as ``CSRF_COOKIE_NEEDS_UPDATE``.
   The response shaper reads that flag and places the fresh token in the patch envelope.
   The client runtime then rewrites ``csrfmiddlewaretoken`` in every form on the document, so forms outside the morphed region do not keep a stale token.
   No project code is needed, see :doc:`/content/topics/partial-rendering/how-it-works` for the envelope.

Different origin without ``CSRF_TRUSTED_ORIGINS``.
   The middleware returns 403.
   Add every origin that posts to the project.

See also
--------

.. seealso::

   :doc:`/content/topics/forms/templates` for the form tag.
   :doc:`/content/topics/forms/validation-rerender` for the re-render flow.
   :doc:`overview` for the broader security picture.
