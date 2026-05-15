.. _howto-form-backend:

Write a Form Action Backend
===========================

Problem
-------

You want every form dispatch to run an extra step such as audit logging or rate limiting, transactional with the dispatch itself.

Solution
--------

Subclass ``next.forms.RegistryFormActionBackend``, override ``dispatch``, and register the dotted path in ``NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]``.

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
           action_name = self._uid_to_name.get(uid)
           if action_name is None:
               return super().dispatch(request, uid)
           response = super().dispatch(request, uid)
           AuditEntry.objects.create(
               action_name=action_name,
               response_status=response.status_code,
           )
           return response

The override calls ``super().dispatch`` to run the standard validation and handler pipeline.
The ``self._uid_to_name`` mapping resolves the UID to an action name.
An unknown UID returns 404 from the parent dispatch, so the override skips it.

Register the backend.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
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

Verification
------------

Submit a form, then query the audit table.
The row appears with the correct action name and response status.

Run the system checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A misconfigured ``DEFAULT_FORM_ACTION_BACKENDS`` entry fires ``next.E044``.
A backend class that does not subclass ``FormActionBackend`` fires ``next.E045``.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/backends` for the topic guide.
   ``examples/audit-forms`` for a worked dual channel audit.
