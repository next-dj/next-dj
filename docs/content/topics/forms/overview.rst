.. _topics-forms-overview:

Forms Overview
==============

The forms subsystem registers Python callables as named actions, exposes them under stable URLs, and dispatches form submissions through a validation pipeline.
A failed submission re-renders the origin page with the bound form, errors, and a fresh CSRF token.
This page covers the mental model behind that pipeline.

.. contents::
   :local:
   :depth: 2

Overview
--------

Five things make a form work.

Form class.
   A subclass of ``next.forms.Form`` or ``next.forms.ModelForm``.

Action.
   A Python callable decorated with ``@action("name", form_class=...)``.

Template tag.
   ``{% form @action="name" %}`` resolves the action UID, posts to its dispatch URL, and injects a CSRF token.

Dispatch endpoint.
   One URL per action that binds POST data and calls the handler when valid.
   A non-POST request to a dispatch URL returns HTTP 405.

Re-render pipeline.
   On validation failure the framework renders the origin page again with the bound form in scope.

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

A submission has four outcomes.

Valid form.
   The dispatcher calls the handler with the bound form and its return value travels back to the client.

Invalid form.
   The dispatcher re-renders the origin page with the bound form in scope and an HTTP 200 status, with no handler called.

Handler-only result.
   A handler-only action that returns ``None`` produces an empty HTTP 204 response.
   See :doc:`actions` for the return type contract.

Bad request.
   A submission missing the ``_next_form_page`` field or pointing at an invalid origin page returns HTTP 400.

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
