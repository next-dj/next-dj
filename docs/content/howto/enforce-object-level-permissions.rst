.. _howto-enforce-object-level-permissions:

Enforce Object-Level Permissions
=================================

Problem
-------

A ``ModelForm`` edits a row loaded from the URL.
Any visitor who can reach the action can submit another user's identifier and edit a row they do not own.
A static ``permission_required`` cannot express "the current user owns this row", because the decision depends on the loaded instance.

Solution
--------

Override ``has_object_permission`` on the ``ModelForm``.
The framework binds the form before the hook runs, so ``self.instance`` is the loaded target.
Return ``True`` when the request owns the row, ``False`` otherwise.
A ``False`` return denies with HTTP 403 and the row is never saved.

Walkthrough
-----------

Load the row from the URL
~~~~~~~~~~~~~~~~~~~~~~~~~~

The edit form loads its instance from a captured URL segment through ``Meta.instance_from_url``.
The route segment ``[slug]`` captures the lookup value.

.. code-block:: python
   :caption: notes/pages/notes/edit/[slug]/page.py

   import next.forms
   from django.http import HttpRequest
   from notes.models import Note

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           instance_from_url = "slug"

       def has_object_permission(self, request: HttpRequest):
           return self.instance.owner_id == request.user.id

The default ``get_initial`` loads ``Note.objects.get(slug=<captured slug>)`` through :func:`~django.shortcuts.get_object_or_404`.
The dispatcher binds the form against that instance, then resolves ``has_object_permission``.
At that point ``self.instance`` is the loaded ``Note``, so the hook compares its owner against the current user.

Combine it with a login requirement
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The object-level hook compares ``request.user``, so the request needs an authenticated user.
Pair the hook with the static ``Meta.login_required`` so an anonymous POST is redirected to the login page before the hook runs.

.. code-block:: python
   :caption: notes/pages/notes/edit/[slug]/page.py — with a login requirement

   class NoteEditForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title", "body"]
           instance_from_url = "slug"
           login_required = True

       def has_object_permission(self, request: HttpRequest):
           return self.instance.owner_id == request.user.id

The static guard runs first and pre-database.
An anonymous visitor is redirected before any application code runs, and the object-level hook only ever sees an authenticated user.

Render the form
~~~~~~~~~~~~~~~

The template renders the form by name.
A ``GET`` still renders the page, because the hook guards the mutation, not the markup.

.. code-block:: jinja
   :caption: notes/pages/notes/edit/[slug]/template.djx

   {% form "note_edit_form" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

Verification
------------

A test signs in as the owner, edits the row, then signs in as another user and confirms the edit is refused.

.. code-block:: python
   :caption: tests/test_object_permissions.py

   from django.contrib.auth import get_user_model
   from next.testing.client import NextClient
   from notes.models import Note

   def test_owner_edits_and_stranger_is_denied(db) -> None:
       User = get_user_model()
       owner = User.objects.create_user("owner")
       stranger = User.objects.create_user("stranger")
       note = Note.objects.create(slug="intro", title="Intro", body="", owner=owner)

       client = NextClient()
       client.force_login(owner)
       client.post_action(
           "note_edit_form",
           {"title": "Intro v2", "body": ""},
           origin=f"/notes/edit/{note.slug}/",
       )
       assert Note.objects.get(pk=note.pk).title == "Intro v2"

       client.force_login(stranger)
       response = client.post_action(
           "note_edit_form",
           {"title": "Hijacked", "body": ""},
           origin=f"/notes/edit/{note.slug}/",
       )
       assert response.status_code == 403
       assert Note.objects.get(pk=note.pk).title == "Intro v2"

The owner's POST binds the form, passes ``has_object_permission``, and saves.
The stranger's POST binds the same row, the hook returns ``False``, and the dispatcher returns a bare HTTP 403 without re-rendering, so the row is unchanged.

A ``form_access_denied`` signal fires on the denial with ``layer="object"`` and ``reason="denied"``.
Connect a receiver to audit refused edits, see :ref:`topics-forms-signals-form-access-denied`.

See Also
--------

.. seealso::

   :ref:`topics-forms-actions-dynamic-guards` for the full hook contract and the view-level companion.
   :doc:`/content/topics/forms/modelforms` for ``instance_from_url`` and the ownership-scoped lookup.
   :doc:`/content/security/di-and-untrusted-input` for the untrusted-input rules behind the loaded row.
   :doc:`/content/howto/require-login-on-pages` for the project-wide login requirement.
