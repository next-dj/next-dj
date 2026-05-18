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
   A Django ``Form`` or ``ModelForm`` mixed with ``next.forms.Form``.
   The mixin allows the framework to register the form identity for re-render.

Action.
   A Python callable decorated with ``@action("name", form_class=...)``.
   The decorator records the callable in a registry and assigns it a stable UID.

Template tag.
   ``{% form @action="name" %}`` resolves the action UID, posts to ``/_next/form/<uid>/``, and injects a CSRF token.

Dispatch endpoint.
   The framework registers one URL per action.
   The endpoint loads the form class, binds POST data, calls the handler when valid.

Re-render pipeline.
   On validation failure the framework finds the origin page module and renders it again with the bound form in scope.

Concepts
--------

Origin Page
~~~~~~~~~~~

The dispatcher needs to know which page rendered the form.
The ``{% form %}`` tag emits a hidden ``_next_form_page`` field with the absolute path to the current ``page.py``.
A submission without that field, or with a path outside ``BASE_DIR``, returns HTTP 400.

Action Name
~~~~~~~~~~~

Every action has a unique name.
The framework hashes the name into a 16 character UID that becomes part of the URL.
Two actions registered under the same name from different handlers are reported by the ``next.E041`` system check.

Use a namespace prefix to keep names from colliding across apps.

.. code-block:: python
   :caption: namespaced action

   from next.forms import action


   @action("save", namespace="notes")
   def save_note(...): ...

The template references ``@action="notes:save"``.

Handler Signature
~~~~~~~~~~~~~~~~~

A handler receives only the parameters it declares.
The :doc:`dependency resolver </content/topics/dependency-injection>` fills each parameter from a provider.

.. code-block:: python
   :caption: handler with multiple parameters

   from django.http import HttpRequest, HttpResponseRedirect

   from next.forms import action
   from next.urls import DUrl


   @action("update_note", form_class=NoteForm)
   def update_note(
       form: NoteForm,
       note_id: DUrl[int],
       request: HttpRequest,
   ) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect("/")

The handler does not see anything it does not ask for.

Validation Outcomes
~~~~~~~~~~~~~~~~~~~

A submission has three outcomes.

Valid form.
   The dispatcher calls the handler with the bound form.
   The return value travels back to the client.

Invalid form.
   The dispatcher returns the rendered origin page with the bound form in scope and an HTTP 200 status.
   No handler is called.

Bad request.
   The submission is missing required dispatch fields or points at an invalid origin page.
   The dispatcher returns HTTP 400 without invoking the handler.

Where to Declare Actions
------------------------

An ``@action`` decorator registers the handler at the time its module is imported.
The framework automatically imports ``page.py`` files (when building URL patterns) and ``component.py`` files (when the components backend initialises), so actions in either location register without any extra wiring.

- ``page.py`` next to the page that uses the action.
- ``component.py`` next to a component that wraps a form.
- Any other module imported from ``AppConfig.ready``. A dedicated ``actions.py`` per-app is a common convention.

A single Python module can register many actions.
Each action stays addressable by name from any template in the project.

Putting It Together
-------------------

A form is the form class, the ``@action`` handler, and the ``{% form %}`` tag working together.
The template tag wires the form to the action, the handler receives the validated form, and a failed validation re-renders the page with the bound form in place.

See :doc:`templates` for a worked end-to-end example and the full ``{% form %}`` tag reference.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`validation-rerender` for the re-render pipeline.
   :doc:`backends` for swapping the dispatch backend.
