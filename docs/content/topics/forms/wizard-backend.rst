.. _topics-forms-wizard-backend:

Wizard Backend
==============

A wizard backend persists per-step draft data between requests.
Every wizard in a project shares one backend, chosen once through ``NEXT_FRAMEWORK["DEFAULT_FORM_WIZARD_BACKEND"]``.
The default backend stores drafts in the Django cache.
This page covers the ``FormWizardBackend`` contract, the bundled ``CacheFormWizardBackend``, its configuration, and a custom-backend recipe.

.. contents::
   :local:
   :depth: 2

The Backend Contract
--------------------

``next.forms.FormWizardBackend`` is an abstract base class with three methods.
Each method receives the ``HttpRequest`` and the wizard storage id.
The storage id is the ``snake_case`` of the wizard class name prefixed with a short hash of the declaring scope, the same page path or dotted module the registration uses, so equally named wizards in different apps never share a draft.
Backends treat the id as an opaque string.

``load(request, wizard_id) -> dict``.
   Returns the ``{step: cleaned_data}`` mapping for the wizard, in step order.

``save_step(request, wizard_id, step, data)``.
   Persists the cleaned data for a single step.

``clear(request, wizard_id)``.
   Drops every stored step for the wizard.

A backend subclasses ``FormWizardBackend`` and implements all three methods.
The wizard reads and writes through this contract alone, so the backend choice is invisible to the wizard class.

The Cache Backend
-----------------

``next.forms.CacheFormWizardBackend`` is the default backend.
It stores drafts in Django's configured cache, namespaced by the session key and the wizard storage id.
Because it keys drafts by session, ``SessionMiddleware`` must be enabled, and the backend creates a session on the first saved step.
Saving a step on a request without session support raises ``ImproperlyConfigured`` instead of silently dropping the draft, and the ``next.W056`` system check flags the misconfiguration at startup.

Durability and worker sharing follow whichever cache the project configures.
A local-memory cache loses drafts on restart and does not share them between workers, which suits development and tests.
A Redis, Memcached, or database cache shares drafts across workers and survives a restart, which suits production.
The wizard does not care which cache is in use.

Stored values must be picklable for any cache backend that serialises its entries.
Keep step fields to values the cache serialiser accepts, such as strings, numbers, booleans, and the dicts and lists built from them.

The backend reads two keys from ``OPTIONS``.

``CACHE_ALIAS``.
   The cache alias to read and write, defaulting to ``"default"``.

``TIMEOUT``.
   The draft lifetime in seconds, defaulting to the ``SESSION_COOKIE_AGE`` setting.

For an anonymous visitor the backend creates a session on the first saved step, so the draft is keyed by that pre-login session.
A draft started before login stays under the pre-login key unless the login flow rotates the session, in which case the visitor loses the draft.
Start a wizard after login, or carry the draft across the rotation, when continuity matters for anonymous starts.

Trust and Tamperability
-----------------------

.. warning::

   Each step is validated once, in isolation, when it is submitted.
   The stored draft then lives in an external store such as the cache or Redis, and ``done`` receives the merged ``cleaned_data`` without re-running any validation.
   A cache that is shared, writable from another process, or otherwise reachable is part of the trust boundary.

   For a sensitive flow, use a signed or encrypted backend so a tampered draft is rejected, and re-check cross-step invariants inside ``done`` rather than trusting the merged dict.

Sensitive Data in Drafts
------------------------

.. warning::

   ``save_step`` writes each step's cleaned data into the store as-is and keeps it until ``TIMEOUT``, which defaults to ``SESSION_COOKIE_AGE``.
   A wizard that collects personal data leaves that data in the cache for the full lifetime.

   Set a short ``TIMEOUT`` for drafts that hold personal data, prefer an encrypted or signed backend over a plain shared cache, and rely on ``done`` clearing the draft after a successful finish.
   The wizard calls ``clear`` after a successful ``done``, so a draft that never completes is what lingers.
   See :doc:`/content/deployment/checklist` for the production review.

Draft Expiry Mid-Wizard
-----------------------

A draft can expire between two steps when ``TIMEOUT`` is short or the cache evicts under pressure.
The backend reads a missing entry as an empty mapping, so ``done`` can receive a partial or empty ``cleaned_data``.

.. warning::

   A naive ``Model.objects.create(**cleaned_data)`` on a partial dict raises a database integrity error instead of a friendly message.
   Validate completeness at the top of ``done``, for example by checking that every required step key is present, and redirect back to the first step when a draft has expired.

Configuration
-------------

``NEXT_FRAMEWORK["DEFAULT_FORM_WIZARD_BACKEND"]`` is a single dict in the same shape as the other framework backends.
It carries a ``BACKEND`` dotted path and an optional ``OPTIONS`` dict.
The framework default points at the cache backend with empty options.

.. code-block:: python
   :caption: framework default

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_WIZARD_BACKEND": {
           "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
           "OPTIONS": {},
       },
   }

A project setting merges shallowly over the default.
Point drafts at a dedicated cache alias and shorten their lifetime through ``OPTIONS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_WIZARD_BACKEND": {
           "BACKEND": "next.forms.wizard.CacheFormWizardBackend",
           "OPTIONS": {"CACHE_ALIAS": "wizards", "TIMEOUT": 3600},
       },
   }

The ``next.E051`` system check fires when the dict is malformed, names a backend that cannot be imported, or names a class that is not a ``FormWizardBackend`` subclass.

Writing a Custom Backend
------------------------

A custom backend subclasses ``FormWizardBackend`` and implements the three methods.
The constructor receives the backend config dict, which lets a backend read its own options.
Point ``DEFAULT_FORM_WIZARD_BACKEND["BACKEND"]`` at the class to use it.

.. code-block:: python
   :caption: access/wizard_backend.py — a Redis-backed wizard store

   import json
   from typing import Any

   import redis
   from django.http import HttpRequest

   from next.forms import FormWizardBackend

   class RedisFormWizardBackend(FormWizardBackend):
       """Wizard draft store backed by a Redis hash per visitor."""

       def __init__(self, config: dict[str, Any] | None = None) -> None:
           options = (config or {}).get("OPTIONS", {})
           url = options.get("URL", "redis://localhost:6379/0")
           self._client = redis.Redis.from_url(url)

       def _key(self, request: HttpRequest, wizard_id: str) -> str:
           return f"next_wizard:{request.session.session_key}:{wizard_id}"

       def load(self, request: HttpRequest, wizard_id: str) -> dict[str, Any]:
           raw = self._client.hgetall(self._key(request, wizard_id))
           return {step.decode(): json.loads(data) for step, data in raw.items()}

       def save_step(
           self, request: HttpRequest, wizard_id: str, step: str, data: dict[str, Any]
       ) -> None:
           self._client.hset(self._key(request, wizard_id), step, json.dumps(data))

       def clear(self, request: HttpRequest, wizard_id: str) -> None:
           self._client.delete(self._key(request, wizard_id))

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_WIZARD_BACKEND": {
           "BACKEND": "access.wizard_backend.RedisFormWizardBackend",
           "OPTIONS": {"URL": "redis://localhost:6379/1"},
       },
   }

The framework instantiates the backend lazily on first use and caches the instance, so the constructor runs once per process.
A signed-cookie store or an external draft service follows the same shape, reading its own options from ``OPTIONS``.

The lazy instance lives behind ``next.forms.wizard_backend_manager``, an instance of ``WizardBackendManager``.
It reads ``DEFAULT_FORM_WIZARD_BACKEND`` on first ``get()`` and caches the result.
Application code never touches it directly.
The framework resets it when settings reload, and the test isolation helper :func:`next.forms.reset_form_registration_state` resets it between cases.

See Also
--------

.. seealso::

   :doc:`wizard` for the wizard class that consumes the backend.
   :doc:`backends` for the analogous form action backend contract.
   :doc:`/content/howto/build-multi-step-wizard` for a step-by-step recipe.
   :doc:`/content/ref/forms` for the public backend API.
