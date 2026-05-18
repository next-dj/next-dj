.. _howto-modelform-crud:

Use ModelForm for CRUD
======================

Problem
-------

You want create, update, and delete pages for a model.

Solution
--------

Use one ``ModelForm`` for create and update, plus a tiny confirmation form for delete.
Register one action per operation and place each action next to the page that triggers it.

Walkthrough
-----------

Define the form.

.. code-block:: python
   :caption: notes/forms.py

   from django import forms
   from next.forms import Form, ModelForm
   from notes.models import Note

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

   class DeleteNoteForm(Form):
       confirm = forms.BooleanField()

Create Page
~~~~~~~~~~~

.. code-block:: python
   :caption: notes/routes/notes/new/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from notes.forms import NoteForm

   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(reverse("next:page_"))

URL names follow the ``page_{path}`` convention where path segments are joined with underscores and captured-parameter brackets are dropped.
See :doc:`/content/topics/file-router` for the full naming rules.

Update Page
~~~~~~~~~~~

.. code-block:: python
   :caption: notes/routes/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from next.forms import action
   from next.pages import context
   from next.urls import DUrl
   from notes.forms import NoteForm
   from notes.models import Note

   @context("form")
   def edit_form(note_id: DUrl["id", int]) -> NoteForm:
       return NoteForm(instance=get_object_or_404(Note, pk=note_id))

   @action("update_note", form_class=NoteForm)
   def update_note(form: NoteForm, note_id: DUrl["id", int]) -> HttpResponseRedirect:
       form.instance = get_object_or_404(Note, pk=note_id)
       form.save()
       return HttpResponseRedirect(reverse("next:page_notes_id", kwargs={"id": note_id}))

Delete Action
~~~~~~~~~~~~~

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from next.urls import DUrl
   from notes.forms import DeleteNoteForm
   from notes.models import Note

   @action("delete_note", form_class=DeleteNoteForm)
   def delete_note(form: DeleteNoteForm, note_id: DUrl["id", int]) -> HttpResponseRedirect:
       Note.objects.filter(pk=note_id).delete()
       return HttpResponseRedirect(reverse("next:page_"))

Templates
~~~~~~~~~

.. code-block:: jinja
   :caption: notes/routes/notes/new/template.djx

   {% form @action="create_note" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/edit/template.djx

   {% form @action="update_note" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Save</button>
   {% endform %}

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   {% form @action="delete_note" %}
     <input type="hidden" name="confirm" value="on">
     <button type="submit" class="danger">Delete</button>
   {% endform %}

Verification
------------

Walk through the flow once.
Create a note, edit it, delete it, and confirm the index reflects each step.

Tests assert the same flow.

.. code-block:: python
   :caption: tests/test_crud.py

   from next.testing.client import NextClient
   from notes.models import Note

   def test_crud_flow(db) -> None:
       client = NextClient()
       client.post_action("create_note", {"title": "T", "body": ""})
       note = Note.objects.get(title="T")
       client.post_action("update_note", {"title": "T2", "body": "", "_url_param_id": note.id})
       assert Note.objects.get(pk=note.id).title == "T2"
       client.post_action("delete_note", {"confirm": "on", "_url_param_id": note.id})
       assert not Note.objects.filter(pk=note.id).exists()

The ``_url_param_id`` key in the POST data mirrors the hidden field that the ``{% form %}`` tag emits for a captured URL parameter.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/modelforms` for the ModelForm topic guide.
   :doc:`/content/topics/forms/validation-rerender` for the re-render flow.
