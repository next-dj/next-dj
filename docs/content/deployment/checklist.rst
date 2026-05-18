.. _deployment-checklist:

Deployment Checklist
====================

Use this checklist before pushing a next.dj project to production.

.. contents::
   :local:
   :depth: 2

Django Settings
---------------

- ``DEBUG`` is ``False``.
- ``ALLOWED_HOSTS`` lists every host the project answers on.
- ``SECRET_KEY`` is unique to the environment and not committed.
- ``CSRF_TRUSTED_ORIGINS`` includes every public origin.
- ``DATABASES`` uses a production engine and a managed credential store.

Run the standard :doc:`Django deployment check <django:howto/deployment/checklist>`.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy

Resolve every warning before deploying.

next.dj Settings
----------------

Tune ``NEXT_FRAMEWORK`` using :doc:`settings` (production-oriented commentary and patterns).
Canonical semantics for each key live in :doc:`/content/ref/settings`.

Static Files
------------

- Run ``uv run python manage.py collectstatic`` during the build.
- Confirm that ``STATIC_ROOT`` is writable and points at the location your web server expects.
- Test that hashed asset URLs land in HTML.
- Configure caching headers on the static origin.

See :doc:`static-files` for the production specific guidance.

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

System Checks
-------------

Run the framework system checks as part of CI and as part of the deployment script.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A clean exit is required for the deployment to proceed.

Smoke Tests
-----------

Hit at least three URLs after the deploy.

- The site index ``/``.
- One captured URL such as ``/notes/1/``.
- One action endpoint through a simulated POST.

The smoke tests confirm that the file router is mounted, the database is reachable, and the dispatcher resolves URLs.

See Also
--------

.. seealso::

   :doc:`static-files` for static file handling.
   :doc:`settings` for production settings.
   :doc:`/content/security/index` for the security checklist.
