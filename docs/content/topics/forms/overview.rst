.. _topics-forms-overview:

Forms Overview
==============

The forms subsystem registers Python callables as named actions, exposes them under stable URLs, and dispatches form submissions through a validation pipeline.
A failed submission re-renders the origin page with the bound form, errors, and a fresh CSRF token.
This page covers the mental model behind that pipeline.

.. contents::
   :local:
   :depth: 2

Concepts
--------

Origin Page
~~~~~~~~~~~

The ``{% form %}`` tag emits a hidden ``_next_form_page`` field with the absolute path to the current ``page.py``, so the dispatcher knows which page to re-render on failure.

Action Name
~~~~~~~~~~~

The framework hashes each action name into a 16 character UID that becomes part of the dispatch URL.
See :doc:`actions` for the namespace syntax and the ``next.E041`` collision rules.

Handler Signature
~~~~~~~~~~~~~~~~~

The :doc:`dependency resolver </content/topics/dependency-injection>` fills each handler parameter from a provider, and the handler receives only the parameters it declares.

.. code-block:: python
   :caption: handler with multiple parameters

   from django.http import HttpRequest, HttpResponseRedirect
   from next.forms import action
   from next.urls import DUrl

   @action("update_note", form_class=NoteForm)
   def update_note(
       form: NoteForm,
       note_id: DUrl["id", int],
       request: HttpRequest,
   ) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")

Validation Outcomes
~~~~~~~~~~~~~~~~~~~

A submission is valid, invalid, handler-only, or malformed.
See :doc:`validation-rerender` for each outcome and its status code.

Where to Declare Actions
------------------------

An ``@action`` decorator registers the handler when its module is imported, so ``page.py`` and ``component.py`` files register without extra wiring.
A single module can register many actions and each stays addressable by name from any template in the project.
See :doc:`actions` for the full placement rules and :doc:`templates` for a worked end-to-end example.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for the re-render pipeline.
   :doc:`backends` for swapping the dispatch backend.
