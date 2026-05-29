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
Each method receives the ``HttpRequest`` and the wizard id, which is the ``snake_case`` of the wizard class name.

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
It stores drafts in Django's configured cache, namespaced by the session key and the wizard id.
Because it keys drafts by session, ``SessionMiddleware`` must be enabled, and the backend creates a session on the first saved step.

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

See Also
--------

.. seealso::

   :doc:`wizard` for the wizard class that consumes the backend.
   :doc:`backends` for the analogous form action backend contract.
   :doc:`/content/howto/build-multi-step-wizard` for a step-by-step recipe.
   :doc:`/content/ref/forms` for the public backend API.
