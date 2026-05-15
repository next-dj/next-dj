.. _intro-tutorial04:

Tutorial Part 4 Forms and Actions
=================================

Goal
----

This part wires create, edit, and delete flows for notes through the form and action subsystem.
By the end the index has a create form, each detail page has edit and delete buttons, and every submission lands in a typed handler that the framework dispatches automatically.

Prerequisites
-------------

You have finished :doc:`tutorial03`.
The ``note_card`` component renders each note and the static collector emits its CSS and JS.

Walkthrough
-----------

Declare the Note Form
~~~~~~~~~~~~~~~~~~~~~

Forms in next.dj are Django forms with one extra base class.
Create ``notes/forms.py``.

.. code-block:: python
   :caption: notes/forms.py

   from django import forms
   from notes.models import Note

   from next.forms import Form


   class NoteForm(Form, forms.ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")


   class DeleteNoteForm(Form):
       confirm = forms.BooleanField(required=True)

``next.forms.Form`` is a mixin that participates in form dispatch.
A plain ``Form`` or ``ModelForm`` cannot be passed to ``@action`` because the dispatch pipeline expects the mixin to register the form identity.

Register the Create Action
~~~~~~~~~~~~~~~~~~~~~~~~~~

Action handlers live wherever you want, the framework discovers them by import.
The natural home is next to the page that exposes the form.
Update ``notes/routes/page.py``.

.. code-block:: python
   :caption: notes/routes/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from notes.forms import NoteForm
   from notes.models import Note

   from next.forms import action
   from next.pages import context


   @context("notes")
   def recent_notes() -> list[Note]:
       return list(Note.objects.all())


   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(reverse("next:page_"))

The signature defines what the dispatcher injects.
``form: NoteForm`` is the validated form instance, populated from the POST body.
You can also ask for ``request``, captured URL parameters, query strings, or any DI marker the resolver knows about.

Render the Create Form
~~~~~~~~~~~~~~~~~~~~~~

Add the form to the index template.
The ``{% form %}`` tag points to the action by name.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <section class="note-create">
     {% form @action="create_note" method="post" class="note-form" %}
       <label>
         Title
         {{ form.title }}
       </label>
       <label>
         Body
         {{ form.body }}
       </label>
       {% if form.errors %}
         <ul class="errors">
           {% for field, errors in form.errors.items %}
             {% for error in errors %}<li>{{ field }} {{ error }}</li>{% endfor %}
           {% endfor %}
         </ul>
       {% endif %}
       <button type="submit">Create</button>
     {% endform %}
   </section>

   <ul class="note-list">
     {% for note in notes %}
       <li>{% component "note_card" %}</li>
     {% endfor %}
   </ul>

The tag resolves the action UID, builds a stable POST URL, and injects a CSRF token automatically.
The ``form`` variable inside the block is the unbound form on a GET and the bound form on a validation failure.

Reload ``/``, submit the form with a title, and confirm that the index lists a new note.

Edit a Note
~~~~~~~~~~~

Create a new page at ``notes/routes/notes/[id]/edit/``.
The page renders the same ``NoteForm`` pre-filled with the existing note.

.. code-block:: python
   :caption: notes/routes/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.forms import NoteForm
   from notes.models import Note

   from next.forms import action
   from next.pages import context
   from next.urls.markers import DUrl


   @context("note")
   def fetch_note(note_id: DUrl[int]) -> Note:
       return get_object_or_404(Note, pk=note_id)


   @context("form")
   def edit_form(note: Note) -> NoteForm:
       return NoteForm(instance=note)


   @action("update_note", form_class=NoteForm)
   def update_note(note_id: DUrl[int], form: NoteForm) -> HttpResponseRedirect:
       form.instance = get_object_or_404(Note, pk=note_id)
       form.save()
       return HttpResponseRedirect(reverse("next:page_notes_id", kwargs={"id": note_id}))

The ``@context("form")`` function publishes a pre-filled bound form to the template.
The framework reuses that name when the action fails validation so the template renders the user input plus the errors.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/edit/template.djx

   <h2>Edit {{ note.title }}</h2>
   {% form @action="update_note" method="post" %}
     <label>Title {{ form.title }}</label>
     <label>Body {{ form.body }}</label>
     <button type="submit">Save</button>
     <a href="{% url 'next:page_notes_id' id=note.id %}">Cancel</a>
   {% endform %}

Add a link from the detail page.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   <article>
     <h2>{{ note.title }}</h2>
     {% if note.body %}<p>{{ note.body }}</p>{% endif %}
     <small>{{ note.created_at|date:"Y-m-d H:i" }}</small>
     <p>
       <a href="{% url 'next:page_notes_id_edit' id=note.id %}">Edit</a>
     </p>
   </article>

Delete a Note
~~~~~~~~~~~~~

Delete uses the same dispatch but does not need its own page because a single button can post directly to the action from the detail template.
Extend the detail template.

.. code-block:: jinja
   :caption: notes/routes/notes/[id]/template.djx

   <article>
     <h2>{{ note.title }}</h2>
     {% if note.body %}<p>{{ note.body }}</p>{% endif %}
     <small>{{ note.created_at|date:"Y-m-d H:i" }}</small>
     <p>
       <a href="{% url 'next:page_notes_id_edit' id=note.id %}">Edit</a>
       {% form @action="delete_note" method="post" id=note.id %}
         <input type="hidden" name="confirm" value="on">
         <button type="submit" class="button-danger">Delete</button>
       {% endform %}
     </p>
   </article>

The ``id=note.id`` argument to ``{% form %}`` propagates the captured URL parameter into the action endpoint so the handler can resolve it.
Add the handler to the detail page.

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.forms import DeleteNoteForm
   from notes.models import Note

   from next.forms import action
   from next.pages import context
   from next.urls.markers import DUrl


   @context("note")
   def fetch_note(note_id: DUrl[int]) -> Note:
       return get_object_or_404(Note, pk=note_id)


   @action("delete_note", form_class=DeleteNoteForm)
   def delete_note(note_id: DUrl[int], form: DeleteNoteForm) -> HttpResponseRedirect:
       get_object_or_404(Note, pk=note_id).delete()
       return HttpResponseRedirect(reverse("next:page_"))

Submit the delete button on a note and the detail page redirects to the index, which no longer lists that note.

How Re-render Works
~~~~~~~~~~~~~~~~~~~

A failing validation does not produce an error page.
The dispatch pipeline saves the bound form and re-renders the origin page with the same context functions, the same DI cache, and the failing form replacing the unbound version.
The user sees their input, the errors next to each field, and a fresh CSRF token.

You can confirm this by submitting the create form with an empty title.
The page re-renders, the title field shows the error, the list of notes still appears, and the URL stays at ``/``.

Checkpoint
----------

The Notes application is functionally complete.

.. code-block:: text
   :caption: notes/ layout

   notes/
     forms.py
     models.py
     migrations/
     _components/
       note_card/
     routes/
       layout.djx
       layout.py
       page.py
       template.djx
       notes/
         layout.djx
         [id]/
           page.py
           template.djx
           edit/
             page.py
             template.djx

Users can create, view, edit, and delete notes.
Every action goes through a typed handler that receives the validated form and any DI markers it asks for.

Common Pitfalls
---------------

Action fires but the page reloads with an empty form.
   Check that the handler returns an ``HttpResponseRedirect`` on success.
   A handler that returns ``None`` renders the page again from scratch.

CSRF token missing.
   ``{% form @action="..." %}`` injects the token automatically, but only on POST forms.
   Plain ``<form method="post">`` markup without the tag still needs ``{% csrf_token %}``.

Edit form does not show the existing data.
   The page must publish a bound form through ``@context("form")``.
   Without the context function the framework renders an unbound form even when the URL points at a real note.

Next Steps
----------

The application works end to end.
The next part shows how to test the pages and how to use the development server effectively.

.. seealso::

   :doc:`tutorial05` writes end-to-end tests with ``NextClient`` and ``SignalRecorder``.
   :doc:`/content/topics/forms/index` covers actions, formsets, and re-render mechanics in depth.
   :doc:`/content/topics/forms/validation-rerender` explains the dispatch pipeline.
