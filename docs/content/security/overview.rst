.. _security-overview:

Security overview
=================

next.dj relies on Django's middleware stack and template engine for the bulk of its security guarantees.
This page lists the Django mechanisms that apply unchanged and the framework specific surfaces that need extra attention.

.. contents::
   :local:
   :depth: 2

Django guarantees used unchanged
--------------------------------

The framework does not bypass any standard :doc:`Django middleware <django:topics/http/middleware>`.

- CSRF tokens flow through the standard :doc:`CsrfViewMiddleware <django:ref/csrf>`.
- Session management uses the standard :doc:`SessionMiddleware <django:topics/http/sessions>`, and the wizard is the one subsystem that writes into the session, see :doc:`sessions-and-auth`.
- Authentication uses ``AuthenticationMiddleware`` and the standard :doc:`auth backends <django:topics/auth/index>`.
- Permissions checking, password hashing, and signed cookies remain unchanged.
- Django template engine :doc:`auto escaping <django:ref/templates/language>` is active for every page and component template.

A standard ``MIDDLEWARE`` block in ``settings.py`` inherits the middleware half of that baseline.
The rest does not travel through middleware at all, template auto escaping being the largest part, and a middleware list can also hold every entry in the wrong order, which is the classic way a standard block still leaves a gap.
Follow the ordering Django's :doc:`middleware reference <django:ref/middleware>` prescribes and confirm the result with ``manage.py check --deploy``.

Framework specific surfaces
---------------------------

The framework adds four surfaces that warrant attention.

File router input.
   Captured URL parameters and query values reach Python through the dependency resolver.
   Treat them as untrusted, see :doc:`di-and-untrusted-input`.

Form dispatch path.
   ``/_next/form/<uid>/`` is the dispatch endpoint for every action.
   See :doc:`csrf-and-forms` for the CSRF flow.

Co-located assets.
   Component and page level CSS and JS ship through the static collector.
   See :doc:`static-assets` for origins, hashes, and integrity.

Partial rendering endpoints.
   Zone GET requests and the SSE stream answer partial clients.
   See :doc:`/content/topics/partial-rendering/sse` for the streaming surface and `Access control`_ for the guard model of both.

Common threats
--------------

CSRF.
   Django middleware plus the framework ``{% form %}`` tag covers the standard form path.
   Manual ``<form>`` elements need explicit ``{% csrf_token %}``.

XSS.
   Django template auto escaping prevents most cases.
   Context functions that return ``mark_safe`` strings or HTML strings bypass escaping.
   Apply ``mark_safe`` only to values you fully control, and never to untrusted input as covered in :doc:`di-and-untrusted-input`.
   The framework escapes ``<``, ``>``, ``&``, U+2028, and U+2029 in the inline init payload, so a serialised value that contains ``</script>`` cannot break out of the tag.
   The remaining risk is that serialised values appear in the page source, so never mark a secret ``serialize=True``, see :doc:`static-assets`.

SQL injection.
   The :doc:`Django ORM <django:topics/db/queries>` uses parameterised queries.
   Raw SQL inside a custom provider must use ``params``.
   See :doc:`di-and-untrusted-input` for the custom-provider validation pattern.

Mass assignment.
   Whitelist editable fields on ``ModelForm``, see :doc:`di-and-untrusted-input` for the rule.

File uploads.
   An upload writes attacker-supplied bytes that the server keeps and later hands to another visitor.
   The dispatch passes ``request.FILES`` into the form and adds no size check, no content sniff, and no filename rewrite of its own, so every limit is Django field validation plus project code.
   Cap the request, validate the bytes rather than the declared media type, generate the stored name on the server, and serve user media from a separate origin as an attachment.
   An accepted SVG is the sharpest edge, because the browser runs script inside it.
   See :doc:`file-uploads`.

Origin spoofing.
   The only page identity a form submission carries is the ``_next_form_origin`` URL path, which the dispatcher resolves through the URLconf with :func:`django.urls.resolve`.
   Before it resolves anything a path-only rule refuses every absolute URL, this host's own included, along with a protocol-relative value such as ``//evil.example`` or ``/\evil.example`` and any value carrying a tab, a newline, or a carriage return, see :doc:`csrf-and-forms`.
   The client never supplies a filesystem path, so an error re-render can target only pages that are reachable through the routing table anyway.
   A value that does not resolve returns HTTP 400 on the paths that need the origin page, a validation failure, a wizard step, and a handler that returns ``None`` and so re-renders the origin in place.
   Every POST reads the field all the same, because the page it names authorizes the request, and a handler that answers with a response of its own needs nothing further from it.
   Substituting the origin of another routed page is contained by that page's own authorization, because the dispatcher resolves the origin and then asks the page it names whether it would serve this request, before any handler runs.
   A page authorizes the submission when its ``render()`` returns no response, and a page with no ``render()`` at all authorizes every caller, exactly as its own view serves every caller.
   A page that short-circuits answers the POST with its own response verbatim, so a substituted origin can only ever name a page the requester may already be served.
   The page is asked against a request restated as a GET of the origin URL, carrying the user, the session and everything else a middleware attached, so a guard keyed on the shape of the request answers the submission as it answers a visit rather than refusing both or neither by accident.
   A denial at this layer emits ``form_access_denied`` with ``layer="page"``, so an audit receiver sees it alongside the two hook layers.
   A guard written inside ``render()`` is therefore the supported page-level check on this path, see `Access control`_, :doc:`/content/topics/pages` and :doc:`/content/topics/forms/validation-rerender`.

Caching and content negotiation.
   A full page response and a partial response can answer the same URL with different bodies, so the framework declares what it negotiated on.
   Every response the routed page view returns carries ``Vary: X-Next-Request, X-Next-Zone, X-Next-Merge, X-Next-Version``, stamped by ``set_partial_vary``, and the partial responses carry the same set.
   A shared cache keyed on the URL alone must honour ``Vary`` or it can store a patch envelope under the URL and hand it to a plain browser visit.
   A ``render`` function that returns its own response short-circuits before the stamp, so a page that answers that way and is cached publicly sets the header itself.

Open redirect.
   ``HttpResponseRedirect`` accepts any URL.
   Validate destinations with ``django.utils.http.url_has_allowed_host_and_scheme`` before passing user input into a redirect target.
   A routing check is not enough, because a protocol-relative value such as ``//evil.com`` can resolve against a wildcard route and still leave the site.
   See :doc:`di-and-untrusted-input` for the worked pattern.
   The partial ``redirect(href, external=True)`` patch is the same escape hatch on the client side, see `Server-authored redirects`_.

Object-level authorization.
   A lookup keyed only on a URL value loads whatever row matches, regardless of who owns it.
   The ModelForm ``Meta.instance_from_url`` lookup is unscoped, so scope it to the user or tenant.
   See :doc:`/content/topics/forms/modelforms` for the ownership-scoped pattern and :doc:`di-and-untrusted-input` for the posted origin path that feeds the lookup.

Server-authored redirects
-------------------------

A partial response can drive a full client navigation with the ``visit`` verb.
The default ``redirect(href)`` admits a path the origin rule accepts and tests any other href with :func:`django.utils.http.url_has_allowed_host_and_scheme` against the host and scheme of the request in flight, so it cannot leave the site and raises ``CrossSiteHrefError`` when it would.
The origin rule comes first so a same-site path longer than Django's 2048-character URL cap still passes, up to the 16384-character origin cap.
The ``external=True`` flag promotes the navigation to a full visit and bypasses that same-host check, which is what an OAuth or a payment-gateway handoff needs.

The flag trusts the caller to author the href.
Pass only a server-controlled destination through it, never a value derived from URL, query, or form input.
A user-supplied href behind ``external=True`` turns the page into an open redirect, the same risk Django guards against in :doc:`its login next-page handling <django:topics/auth/default>`.
The rule mirrors the plain ``HttpResponseRedirect`` case above.
Validate or whitelist any destination that traces back to a request value before it reaches the patch.

Access control
--------------

Form actions are unauthenticated by default.
The ``/_next/form/<uid>/`` endpoint accepts a POST from any visitor, so a registered edit or delete action runs without an identity check unless the handler adds one.

Enforce access at one of these layers.

- Declare ``Meta.login_required`` and ``Meta.permission_required`` on the form class, or the same keywords on ``@action``, for a static guard checked before any application code, see :ref:`topics-forms-actions-guards`.
- Override ``check_permissions`` or ``has_object_permission`` on the form class for a per-request decision against the database, the tenant, or the loaded row, see :ref:`topics-forms-actions-dynamic-guards`.
- Check ``request.user.is_authenticated`` and ownership inside ``on_valid`` before ``self.save()``.
- Return a response from the ``render()`` of the page that carries the form, which the dispatcher runs on the submission before any handler, so one check covers the visit and every action posted from that page, see :doc:`/content/topics/pages`.
- Apply a project-wide login requirement through middleware, see :doc:`/content/howto/require-login-on-pages`.
- Enforce a policy in a custom form action backend that wraps every dispatch.

An action that mutates data and an action that loads an instance through ``instance_from_url`` both need this guard.
The :ref:`howto-enforce-object-level-permissions` recipe shows the owner-only edit on a ``ModelForm``.

Rate limiting.
   The ``/_next/form/<uid>/`` endpoint ships no built-in throttling, so a guarded action still answers as many requests as a client sends.
   A custom ``FormActionBackend`` that wraps every dispatch is the documented extension point for rate limiting, see the ``FORM_ACTION_BACKENDS`` example in :doc:`/content/deployment/settings`.

A zone GET carries no endpoint of its own.
It travels the page's own URL through the same routed view, so the page's guards, its middleware, and its ``render`` function all apply exactly as they do on a full visit, and a page that is closed to a visitor stays closed for every zone inside it.

An SSE stream is a response the project returns from one of its own pages, not an endpoint the framework mounts.
The authorization of that page is therefore the only gate on the stream, and a stream that fans out rows a visitor may not read needs the filter in the generator that produces them, see :doc:`/content/topics/partial-rendering/sse`.

Every zone morph enforces page-level access on its own.
A ``morph(zone=..., page=...)`` onto a foreign page re-runs that page's authorization chain and raises ``ForeignPageNotAuthorizedError`` on a denial or ``DynamicForeignPageError`` for a dynamic body, see :doc:`/content/topics/partial-rendering/reference`.
A ``morph(zone=...)`` without ``page=`` renders the posted origin page and authorizes it the same way, raising ``ForeignPageNotAuthorizedError`` when that page refuses the requester.

What a custom backend must preserve
-----------------------------------

Replacing a backend replaces the code that enforces a guarantee, so each family carries an obligation the bundled implementation meets for you.

- A ``FormActionBackend`` whose ``dispatch`` drives the pipeline by hand rather than delegating to ``FormActionDispatch.dispatch`` runs no ``ActionGuard`` check, so ``Meta.login_required`` and ``Meta.permission_required`` stop applying to every action it serves, and ``check_permissions`` and ``has_object_permission`` stop running with them.
- The same hand-rolled ``dispatch`` also skips the authorization of the resolved origin page, so a guard living in a page's ``render()`` stops covering the submissions that backend serves.
- A ``FormWizardBackend`` keeps each draft reachable only by the requester that wrote it, which both bundled backends get by keying on the session, so a store keyed on anything a client supplies hands one visitor another's partially entered data.
- A ``StaticBackend`` that overrides a tag renderer takes on the URL escaping, because the finished tag is spliced past the template engine and the bundled renderers escape ``str(url)`` through :func:`html.escape`, see :doc:`static-assets`.
- A router backend that contributes patterns of its own sends requests to views the file router never built, so those views carry no page guard of the framework's and their authorization lives in the view itself or in middleware.
- A partial protocol backend serialises the envelope a page already produced, so it inherits the access decisions above and adds one of its own only when it reads values the shaper did not put there.
- A port implementation rebound in ``AppConfig.ready`` replaces framework code rather than wrapping it, so an implementation behind ``partial_shaper_slot`` owns the ``Vary`` stamp described under `Common threats`_ and the foreign-page authorization chain, see :doc:`/content/ref/ports`.

Production hardening
--------------------

Every production setting the framework cares about is a stock Django one, and ``uv run python manage.py check --deploy`` reports each one that is unset or unsafe, so run it and resolve every warning against Django's :doc:`deployment checklist <django:howto/deployment/checklist>`.
The one entry worth naming here is ``CSRF_TRUSTED_ORIGINS``, because the form dispatch path posts to ``/_next/form/<uid>/`` and a submission from another origin fails the middleware check without it, see :doc:`csrf-and-forms`.

System checks
-------------

The framework system checks cover configuration mistakes that affect security.

- ``next.E041`` reports two actions registered under the same name from different handlers.
- ``next.E045`` reports a form action backend that does not subclass ``FormActionBackend``.
- ``next.E020`` reports a component registered more than once within the same scope.
- ``next.E046`` reports one shared action name declared by two different modules, where a lookup by bare name resolves to whichever module imported first.
- ``next.W060`` reports an action that declares ``permission_required`` while ``django.contrib.auth`` is out of ``INSTALLED_APPS``, so the guard described under `Access control`_ cannot resolve users or permissions.
- ``next.W061`` reports an action that declares ``Meta.success_message`` while the messages framework is not fully installed, which makes the submission raise ``MessageFailure``.

Run them with ``uv run python manage.py check``.

See also
--------

.. seealso::

   :doc:`csrf-and-forms` for the form pipeline.
   :doc:`sessions-and-auth` for the session and the authenticated user.
   :doc:`static-assets` for the static pipeline.
   :doc:`di-and-untrusted-input` for the dependency surface.
   :doc:`file-uploads` for user-supplied files and the media origin.
   :doc:`/content/topics/static-assets/js-context` for runtime script options that interact with CSP.
   :doc:`/content/deployment/checklist` for the full pre-deploy review.
   :doc:`reporting` for vulnerability disclosure.
