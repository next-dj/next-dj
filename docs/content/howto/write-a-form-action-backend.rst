.. _howto-form-backend:

Write a Form Action Backend
===========================

Problem
-------

You want every form dispatch to run an extra step such as audit logging or rate limiting, transactional with the dispatch itself.

Solution
--------

Subclass ``next.forms.RegistryFormActionBackend``, override ``dispatch``, and register the dotted path in ``NEXT_FRAMEWORK["FORM_ACTION_BACKENDS"]``.

Walkthrough
-----------

Write the backend.

.. code-block:: python
   :caption: notes/backends.py

   from django.http import HttpRequest, HttpResponse
   from next.forms import RegistryFormActionBackend
   from notes.models import AuditEntry

   class AuditedFormActionBackend(RegistryFormActionBackend):
       """Registry backend that writes an audit row per dispatch."""

       def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
           key = self._uid_to_name.get(uid)
           if key is None:
               return super().dispatch(request, uid)
           _scope_key, action_name = key
           response = super().dispatch(request, uid)
           AuditEntry.objects.create(
               action_name=action_name,
               response_status=response.status_code,
           )
           return response

The override calls ``super().dispatch`` to run the standard validation and handler pipeline.
The ``self._uid_to_name`` mapping is a private UID index.
It maps each UID to a ``(scope_key, name)`` tuple.
Unpack the tuple to extract the bare action name.
An unknown UID raises ``Http404`` from the parent dispatch, so the override skips the audit row for it.

Register the backend.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.AuditedFormActionBackend"},
       ]
   }

The custom backend replaces ``RegistryFormActionBackend`` because it already inherits every default behaviour.

Block a Dispatch
~~~~~~~~~~~~~~~~

Return an ``HttpResponse`` before calling ``super().dispatch`` to short circuit.

.. code-block:: python
   :caption: notes/backends.py

   from django.http import HttpResponse
   from next.forms import RegistryFormActionBackend

   class RateLimitedBackend(RegistryFormActionBackend):
       def dispatch(self, request, uid) -> HttpResponse:
           if self._over_limit(request):
               return HttpResponse(status=429)
           return super().dispatch(request, uid)

       def _over_limit(self, request) -> bool:
           return False

Read an Option
~~~~~~~~~~~~~~

A backend reads its own settings from the ``OPTIONS`` dict of its config entry.
The factory passes the whole config entry to the constructor, so declare ``__init__`` and pull ``OPTIONS`` out of it.

.. code-block:: python
   :caption: notes/backends.py

   from typing import Any
   from next.forms import RegistryFormActionBackend

   class RateLimitedBackend(RegistryFormActionBackend):
       def __init__(self, config: dict[str, Any] | None = None) -> None:
           super().__init__(config)
           options = (config or {}).get("OPTIONS", {})
           self._limit = options.get("RATE_PER_MINUTE", 60)

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "FORM_ACTION_BACKENDS": [
           {
               "BACKEND": "notes.backends.RateLimitedBackend",
               "OPTIONS": {"RATE_PER_MINUTE": 30},
           },
       ]
   }

Forward ``config`` to ``super().__init__`` so the registry is set up before you read any option.

Change the Invalid Envelope
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The default backend answers an invalid submission with HTTP 200, the re-rendered origin page, and the ``X-Next-Form``/``X-Next-Action`` headers.
Override ``shape_response`` to change the envelope without touching the HTML, for example for a client that expects 422 on validation errors.

.. code-block:: python
   :caption: notes/backends.py

   from django.http import HttpRequest, HttpResponse
   from next.forms import ActionOutcome, ActionOutcomeKind, RegistryFormActionBackend

   class UnprocessableBackend(RegistryFormActionBackend):
       def shape_response(self, request: HttpRequest, outcome: ActionOutcome) -> HttpResponse:
           response = super().shape_response(request, outcome)
           if outcome.kind is ActionOutcomeKind.INVALID:
               response.status_code = 422
           return response

``outcome.kind`` discriminates the pipeline outcomes, so the handler-result and wizard-advance envelopes stay default.
``ActionOutcomeKind.INVALID`` only ever represents a failed validation.
A valid submission whose handler returns ``None`` leaves the pipeline as a ``RESULT`` outcome whose default envelope re-renders the origin without re-entering ``shape_response``, so the 422 override never touches successful submissions.
Override ``render_invalid_page`` instead when only the error HTML changes and the envelope stays as shipped.
See :doc:`/content/topics/forms/backends` for the two customisation layers and the ``ActionOutcome`` fields.

Surface Actions to the System Checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The forms system checks collect action metadata from every configured backend through ``iter_actions()``, which yields one ``ActionMeta`` per stored action.
A subclass of ``RegistryFormActionBackend`` inherits a working implementation.
A from-scratch backend overrides the hook so its actions participate in checks such as ``next.W054`` and ``next.W060``.

.. code-block:: python
   :caption: notes/backends.py

   from collections.abc import Iterable
   from next.forms.backends import ActionMeta

   class CustomBackend(FormActionBackend):
       def iter_actions(self) -> Iterable[ActionMeta]:
           yield from self._metas.values()

The yielded dicts carry the action ``name``, the target, the ``uid``, and the access ``guard``, the same shape ``get_meta`` returns.

Verification
------------

Submit a form, then query the audit table.
The row appears with the correct action name and response status.

Run the system checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A misconfigured ``FORM_ACTION_BACKENDS`` entry fires ``next.E044``.
A backend class that does not subclass ``FormActionBackend`` fires ``next.E045``.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/backends` for the topic guide.
   ``examples/audit-forms`` for a worked audit backend.
