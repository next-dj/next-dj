.. _howto-enforce-object-level-permissions:

Enforce object-level permissions
=================================

Problem
-------

A ``ModelForm`` edits a row loaded from the URL.
Any visitor who can reach the action can submit another user's identifier and edit a row they do not own.
A model permission such as ``notes.change_note`` cannot express "the current user owns this row", because the decision depends on the loaded instance.

Solution
--------

Declare the rule once as a predicate in ``permissions.py`` and register it under a permission name.
Name that permission in ``Meta.permission_required``, and the dispatcher checks it against the bound row with ``user.has_perm(perm, form.instance)``.
The ``{% has_perm %}`` tag asks the same question in the template, so the page hides the edit link from exactly the users the form refuses.
A ``False`` answer denies the submission with HTTP 403 and the row is never saved.

Walkthrough
-----------

Declare the predicate
~~~~~~~~~~~~~~~~~~~~~

The ``permissions.py`` of the application registers the permission.
The ``next`` application imports it at startup, so it needs no import of its own.

.. code-block:: python
   :caption: notes/permissions.py

   from next.permissions import add_perm, predicate

   @predicate
   def is_owner(user, note):
       return note is not None and note.owner_id == user.pk

   add_perm("notes.edit_note", is_owner)

``edit_note`` is a codename no model defines, so the predicate alone answers it.
``note`` is ``None`` on the model-level question, and ``is_owner`` refuses it, since no row has an owner yet.
A predicate that never looks at the row takes the user alone, ``def is_editor(user)``, and one that looks at neither takes no parameter, ``def notes_open()``.
See :ref:`topics-permissions` for composing predicates with ``&``, ``|``, and ``~``.

List the backend
~~~~~~~~~~~~~~~~

``ObjectPermissionBackend`` answers ``user.has_perm()`` from the registered predicates, next to the ``ModelBackend`` that logs users in.

.. code-block:: python
   :caption: settings.py

   AUTHENTICATION_BACKENDS = [
       "django.contrib.auth.backends.ModelBackend",
       "next.permissions.ObjectPermissionBackend",
   ]

A project that registers a permission without the backend fails ``manage.py check`` with ``next.E162``.
With two backends listed, a view that logs in a user :func:`~django.contrib.auth.authenticate` did not return, such as a fresh signup, passes ``backend="django.contrib.auth.backends.ModelBackend"`` to :func:`~django.contrib.auth.login`, which raises ``ValueError`` without it.

Guard the form
~~~~~~~~~~~~~~

In this project ``visible_to(scope)`` returns the notes a user may read, their own and every note marked ``shared``, and the permission decides which of those they may edit.
The edit form loads its row through ``DObject`` in ``get_form_kwargs`` and names the permission in ``Meta.permission_required``.

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/edit/page.py

   from typing import Any

   from notes.models import Note

   import next.forms
   from next.deps import DObject

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           permission_required = "notes.edit_note"

       @classmethod
       def get_form_kwargs(cls, note: DObject[Note]) -> dict[str, Any]:
           return {"instance": note}

``permission_required`` implies a login, so an anonymous POST is redirected to ``LOGIN_URL`` before any application code runs.
``DObject`` reads the row through ``visible_to(scope)``, so a row the user cannot read answers 404 before any permission runs, see :ref:`topics-di-dobject`.
The dispatcher binds the form against the loaded row and then asks ``user.has_perm("notes.edit_note", form.instance)``, before ``is_valid`` and the handler.

.. warning::

   ``Meta.instance_from_url`` loads the row through an unscoped :func:`~django.shortcuts.get_object_or_404` on the model, and the registered permission is checked only after that row has loaded.
   A refused user then gets 403 for an id that exists and 404 for one that does not, which lets them probe which ids exist.
   Load the row through ``DObject`` as above, so a row outside the scope answers 404 exactly like a missing one, see :doc:`/content/topics/forms/modelforms`.

Render the link and the form
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The note page, whose context carries ``note``, shows the edit link only to a user who may follow it.

.. code-block:: jinja
   :caption: notes/pages/notes/[int:note]/template.djx

   {% has_perm "notes.edit_note" note %}
     <a href="/notes/{{ note.pk }}/edit/">Edit</a>
   {% endhas_perm %}

The edit page renders the form by name.
A ``GET`` still renders it, because the guard protects the mutation, not the markup.

.. code-block:: jinja
   :caption: notes/pages/notes/[int:note]/edit/template.djx

   {% form "note_edit_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

Check against the request
~~~~~~~~~~~~~~~~~~~~~~~~~

A predicate sees the user and the row and nothing else.
A rule that reads the request, such as the tenant a middleware attaches to it, overrides ``has_object_permission`` on the form, which runs after the registered permissions with ``self.instance`` bound.

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/edit/page.py

   from typing import Any

   from django.http import HttpRequest
   from notes.models import Note

   import next.forms
   from next.deps import DObject

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           permission_required = "notes.edit_note"

       @classmethod
       def get_form_kwargs(cls, note: DObject[Note]) -> dict[str, Any]:
           return {"instance": note}

       def has_object_permission(self, request: HttpRequest) -> bool:
           return self.instance.tenant_id == request.tenant.id

The hook is dependency-injected, so it declares only the parameters it reads, see :ref:`topics-forms-actions-dynamic-guards`.

Verification
------------

A test signs in as the owner and edits a shared note, then signs in as another user, who may read the note but not edit it, and finally asks for a private note of the owner.

.. code-block:: python
   :caption: tests/test_object_permissions.py

   from django.contrib.auth import get_user_model
   from notes.models import Note

   from next.testing.client import NextClient

   def test_owner_edits_and_stranger_is_denied(db) -> None:
       User = get_user_model()
       owner = User.objects.create_user("owner")
       stranger = User.objects.create_user("stranger")
       shared = Note.objects.create(title="Intro", body="", owner=owner, shared=True)
       private = Note.objects.create(title="Diary", body="", owner=owner)

       client = NextClient()
       client.force_login(owner)
       client.post_action(
           "note_edit_form",
           {"title": "Intro v2", "body": ""},
           origin=f"/notes/{shared.pk}/edit/",
       )
       assert Note.objects.get(pk=shared.pk).title == "Intro v2"

       client.force_login(stranger)
       response = client.post_action(
           "note_edit_form",
           {"title": "Hijacked", "body": ""},
           origin=f"/notes/{shared.pk}/edit/",
       )
       assert response.status_code == 403
       assert Note.objects.get(pk=shared.pk).title == "Intro v2"

       response = client.post_action(
           "note_edit_form",
           {"title": "Hijacked", "body": ""},
           origin=f"/notes/{private.pk}/edit/",
       )
       assert response.status_code == 404

The owner's POST loads the shared note, passes ``notes.edit_note`` on it, and saves.
The stranger's POST loads the same note, since ``visible_to`` lets them read it, and the predicate refuses it, so the dispatcher raises :exc:`~django.core.exceptions.PermissionDenied`.
Django renders the denial as a bare HTTP 403 without re-rendering the origin, so the row is unchanged.
The private note lies outside the scope of the stranger, so ``DObject`` answers 404 and no permission runs.

A ``form_access_denied`` signal fires on the 403 with ``layer="object"`` and ``reason="denied"``.
Connect a receiver to audit refused edits, see :ref:`topics-forms-signals-form-access-denied`.

See also
--------

.. seealso::

   :ref:`topics-permissions` for predicates, the backend, the tag, and django-rules side by side.
   :ref:`topics-forms-actions-dynamic-guards` for the full hook contract and the view-level companion.
   :doc:`/content/topics/forms/modelforms` for ``DObject`` in ``get_form_kwargs`` and the unscoped ``instance_from_url``.
   :doc:`/content/security/di-and-untrusted-input` for the untrusted-input rules behind the loaded row.
   :doc:`/content/howto/require-login-on-pages` for the project-wide login requirement.
