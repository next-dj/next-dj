.. _howto-form-backend:

Write a Form Action Backend
===========================

Problem
-------

You want every form dispatch to go through a custom validation step such as audit logging, rate limiting, or country based blocking.

Solution
--------

Subclass ``next.forms.backends.FormActionBackend``, implement ``dispatch``, and add the dotted path to ``NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]``.

Walkthrough
-----------

Write the backend.

.. code-block:: python
   :caption: notes/backends.py

   from django.http import HttpRequest, HttpResponse

   from next.forms.backends import FormActionBackend
   from next.forms.dispatch import DispatchContext

   from notes.models import AuditEntry


   class AuditBackend(FormActionBackend):
       def dispatch(
           self,
           request: HttpRequest,
           context: DispatchContext,
       ) -> HttpResponse | None:
           AuditEntry.objects.create(
               user=getattr(request.user, "pk", None),
               action=context.action_name,
           )
           return None

The backend returns ``None`` to continue the chain.
Return an ``HttpResponse`` to short circuit, or raise ``ValidationError`` to surface a form error.

Register the backend.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": extend_default_backend(
           "DEFAULT_FORM_ACTION_BACKENDS",
           "notes.backends.AuditBackend",
           position="after",
           target="next.forms.backends.OriginPageBackend",
       ),
   }

The audit backend now runs after the origin page check and before the form dispatch.

Per Action Backends
~~~~~~~~~~~~~~~~~~~

Apply a backend to a single action through the decorator argument.

.. code-block:: python
   :caption: per action

   from next.forms import action


   @action("create_note", form_class=NoteForm, backends=["notes.backends.AuditBackend"])
   def create_note(form): ...

The per action backends run in addition to the project defaults.

Verification
------------

Submit a form, then query the audit table.
The row appears with the correct action name and user.

Check the system check output.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

A misconfigured dotted path or a class that does not inherit ``FormActionBackend`` fires ``next.E060`` or ``next.E061``.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/backends` for the topic guide.
   :doc:`extend-a-default-backend` for the helper details.
