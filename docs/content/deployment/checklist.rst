.. _deployment-checklist:

Deployment checklist
====================

Use this checklist before pushing a next.dj project to production.

.. contents::
   :local:
   :depth: 2

Django settings
---------------

- ``DEBUG`` is ``False``.
- ``ALLOWED_HOSTS`` lists every host the project answers on.
- ``SECRET_KEY`` is unique to the environment and not committed.
- ``DATABASES`` uses a production engine and a managed credential store.

The transport and cookie settings below are the hardening set a next.dj project ships with, and :doc:`/content/security/overview` explains what each one defends against.

- ``SECURE_SSL_REDIRECT = True`` redirects every HTTP request to HTTPS.
- ``SECURE_CONTENT_TYPE_NOSNIFF = True`` blocks MIME-type sniffing.
- ``SECURE_HSTS_SECONDS = 31536000`` sends a one-year HSTS header.
- ``SECURE_HSTS_INCLUDE_SUBDOMAINS = True`` extends HSTS to every subdomain.
- ``SECURE_HSTS_PRELOAD = True`` allows submission to the HSTS preload list.
- ``SESSION_COOKIE_SECURE = True`` sends the session cookie only over HTTPS.
- ``CSRF_COOKIE_SECURE = True`` sends the CSRF cookie only over HTTPS.
- ``CSRF_TRUSTED_ORIGINS`` lists every public origin a form may be submitted from.

Run the standard :doc:`Django deployment check <django:howto/deployment/checklist>`.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy

Resolve every warning before deploying.

next.dj settings
----------------

Tune ``NEXT_FRAMEWORK`` using :doc:`settings` (production-oriented commentary and patterns).
Canonical semantics for each key live in :doc:`/content/ref/settings`.

Page metadata
-------------

- ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]["base"]`` names the public origin, so the canonical links, the social images, the sitemap locations, and the ``Sitemap:`` line of the robots file stop following the request host.
- ``NEXT_FRAMEWORK["METADATA"]["NOINDEX"]`` is ``False`` in production and ``True`` on every staging host.
- Run ``uv run python manage.py check --deploy --tag seo`` and resolve or silence every audit warning by its id.

See :doc:`/content/topics/seo/auditing` for the two tiers of metadata checks.

Sitemap and robots
------------------

Review these when a page root carries a ``sitemap.py``, a ``robots.py``, or a ``robots.txt``.

- ``django.contrib.sitemaps`` is in ``INSTALLED_APPS`` and the Django template backend keeps ``APP_DIRS``, otherwise ``next.E112`` reports the missing templates.
- ``base`` is set on every deployment that installs ``django.contrib.sites``, because without it the sitemap reads the ``Site`` row and the row ships as ``example.com``.
- ``include("next.seo.urls")`` sits at the root of the URLconf when ``include("next.urls")`` is under a prefix or inside ``i18n_patterns``, otherwise ``next.W099`` reports the unreachable routes.
- Fetch ``/sitemap.xml`` and ``/robots.txt`` on the deployed host and read the first ``<loc>`` and the ``Sitemap:`` line for the public origin.
- A sitemap over a large table declares ``cache`` and the default cache is shared across every worker.

See :doc:`/content/topics/seo/sitemaps` and :doc:`/content/topics/seo/robots` for the two files.

Wizard drafts
-------------

Review these when the project ships a ``FormWizard``.

- The session or cache store behind the wizard backend is durable and shared across every worker.
- Drafts that hold personal data sit in a signed or encrypted store and expire on their own.
- ``CacheFormWizardBackend`` reads that expiry from its ``TIMEOUT`` option, which falls back to ``SESSION_COOKIE_AGE`` when the option is absent.
- The default ``SessionFormWizardBackend`` keeps drafts in the session, so ``SESSION_COOKIE_AGE`` and the session engine decide how long they survive.

See :doc:`/content/topics/forms/wizard-backend` for the backend trade-offs.

Static files
------------

- Run ``uv run python manage.py collectstatic`` during the build.
- Confirm that ``STATIC_ROOT`` is writable and points at the location your web server expects.
- Test that hashed asset URLs land in HTML.
- Configure caching headers on the static origin.

See :doc:`static-files` for the production specific guidance.

Partial rendering
-----------------

Review these when the project serves partial responses.

- Run ``collectstatic`` before the first worker of the new release accepts traffic, so the version a response stamps names assets the browser can already fetch.
- Plan for the window in a rolling deploy where a client that loaded its page from the old release asks the new one for a zone.
- Set ``STATIC_VERSION`` to a per-release string when no hashing staticfiles storage is configured, or pin the ``VERSION`` option of the ``PARTIAL_BACKENDS`` entry when the partial stamp has to differ from the one the asset URLs carry.
- Confirm the version a deployed response stamps changes between releases, because a version that never moves leaves the guard silent.

Every partial response carries the asset version in its ``X-Next-Version`` header.
A safe-method zone request that asserts a different version answers HTTP 409 with an empty body, and the runtime turns that one answer into a single full visit of the page that owns the zone.
A client spanning two versions therefore pays one full page load and then runs on the new assets, rather than patching new HTML into a page whose scripts and styles come from the release before.

The ``VERSION`` option is unset by default, and an unset option reads ``STATIC_VERSION`` before it hashes the staticfiles manifest the configured storage writes.
A deployment that offers neither resolves a stable string that never changes and a guard that never fires, which ``manage.py check --deploy`` reports as ``next.W083``.
An entry that names the sentinel ``"manifest"`` states the manifest as a requirement, and ``manage.py check`` reports ``next.W069`` when the storage cannot meet it.

See :doc:`/content/topics/partial-rendering/reference` for the header and the status codes.

Database
--------

- Apply every migration before starting the new process.
- Run ``uv run python manage.py migrate --plan`` to confirm the migration set.
- Take a snapshot before applying destructive or high-risk schema changes.

Server
------

- Pick WSGI or ASGI based on whether the project uses streaming responses, SSE, or websockets.
- Configure the worker count based on the expected concurrency.
- Set the worker timeout above the slowest expected handler.

See :doc:`wsgi-asgi` for the server choice.

Monitoring
----------

- Forward ``page_rendered`` and ``action_dispatched`` to your metrics pipeline.
- Forward ``form_validation_failed`` to alerting when failure rate exceeds the baseline.
- Track ``router_reloaded`` if the project mounts a dynamic router.
- Forward ``sse_stream_opened``, ``sse_stream_closed``, and ``zone_rendered`` when the project uses partial rendering.

System checks
-------------

Run the framework system checks as part of CI and as part of the deployment script.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy

A clean exit is required for the deployment to proceed.

The ``--deploy`` flag is what matters here, because eight framework checks are registered as deployment checks and a plain ``check`` never runs them.
Three report ``next.E017`` for a ``page.py`` that raises on import, ``next.E084`` for a ``component.py`` that does, and ``next.E072`` for a composed page template that does not compile.
Each one costs a full walk of the page tree, which is why it is paid once per deploy rather than on every management command, and each one names a failure a visitor would otherwise meet as a 404, a silently stripped body, or a 500.
``next.W083`` reports an asset version that never moves, and the four SEO audits, ``next.W089`` to ``next.W096``, grade the folded metadata of every page.

See :ref:`ref-system-checks` for the full catalog.

Access control tests
--------------------

Keep at least one negative test per guarded action in the suite, an anonymous POST that redirects to ``LOGIN_URL`` and an unauthorised POST that answers ``403``.
A guard dropped in a refactor breaks no test that posts as the owner, so the denial assertion is the only thing that fails when the protection disappears.
Run the suite with CSRF enforcement on at least one client as well.

See :ref:`howto-test-actions` for the three guard layers and the status each one produces.

Smoke tests
-----------

Hit at least three URLs after the deploy.

- The site index ``/``.
- One captured URL such as ``/notes/<existing-id>/``.
- One action endpoint through a simulated POST.

The smoke tests confirm that the file router is mounted, the database is reachable, and the dispatcher resolves URLs.

See also
--------

.. seealso::

   :doc:`static-files` for static file handling.
   :doc:`settings` for production settings.
   :doc:`/content/security/index` for the security checklist.
