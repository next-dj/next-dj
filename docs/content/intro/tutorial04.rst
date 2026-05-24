.. _intro-tutorial04:

Forms and Actions
=================

Goal
----

This part wires create, edit, and delete flows for notes through the form and action subsystem.
By the end the index has a create form, each detail page has edit and delete buttons, and every submission lands in a typed action handler that the framework dispatches automatically.

Prerequisites
-------------

You have finished :doc:`tutorial03`.
The ``note_card`` component renders each note and the static collector emits its CSS and JS.

Action handlers resolve ``request``, the form, and URL segments through the same dependency injection used by pages, covered in :doc:`/content/topics/dependency-injection`.

Walkthrough
-----------

Declare the Note Form
~~~~~~~~~~~~~~~~~~~~~

Forms in next.dj are Django forms with one extra base class.
Create ``notes/forms.py``.

.. code-block:: python
   :caption: notes/forms.py

   from notes.models import Note
   from next.forms import BooleanField, Form, ModelForm

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

   class DeleteNoteForm(Form):
       confirm = BooleanField(required=True)

``next.forms.ModelForm`` and ``next.forms.Form`` are the framework form base classes.
They participate in :doc:`form dispatch </content/topics/forms/index>`.
A plain Django ``Form`` or ``ModelForm`` cannot be passed to ``@action`` because the dispatch pipeline expects the framework base class.
``next.forms`` re-exports the common Django form fields and widgets used in this tutorial, so ``BooleanField`` and the rest are importable from one place.
Import other fields directly from :mod:`django.forms` when you need them.

Register the Create Action
~~~~~~~~~~~~~~~~~~~~~~~~~~

An ``@action`` action handler registers when its module is imported.
Placing it in the page's ``page.py`` means the file router imports it on the first request, so the natural home is next to the page that exposes the form.
Update ``notes/pages/page.py``.
The ``inherit_context=True`` flag on the three layout-scope callables stays from :doc:`tutorial02` so each value still reaches every descendant page.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from notes.forms import NoteForm
   from notes.models import Note
   from next.forms import action
   from next.pages import context

   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"

   @context("tagline", inherit_context=True)
   def tagline() -> str:
       return "A small tutorial application."

   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

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
   :caption: notes/pages/template.djx

   <section class="note-create">
     {% form @action="create_note" class="note-form" %}
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
The rendered form always uses ``method="post"``, so you do not write the method yourself.
The ``form`` variable inside the block is the unbound form on a GET and the bound form on a validation failure.

Reload ``/``, submit the form with a title, and confirm that the index lists a new note.

Edit a Note
~~~~~~~~~~~

Create a new page at ``notes/pages/notes/[id]/edit/``.
The action binds ``NoteForm`` to the existing note through a ``form_class`` factory.

.. code-block:: python
   :caption: notes/pages/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.forms import NoteForm
   from notes.models import Note
   from next.forms import action
   from next.pages import context
   from next.urls import DUrl

   @context("note")
   def fetch_note(note_id: DUrl["id", int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

   def note_edit_form(note_id: DUrl["id", int]) -> tuple[type[NoteForm], dict]:
       note = get_object_or_404(Note, pk=note_id)
       return NoteForm, {"instance": note}

   @action("update_note", form_class=note_edit_form)
   def update_note(form: NoteForm) -> HttpResponseRedirect:
       note = form.save()
       return HttpResponseRedirect(reverse("next:page_notes_id", kwargs={"id": note.id}))

The factory passed to ``form_class`` is dependency-resolved at dispatch time, so it receives the captured URL ``id`` and returns the form class paired with the ``instance`` to bind.
The dispatcher builds and validates that bound form before it calls ``update_note``, so the handler only saves it.
An ``id`` that matches no note makes ``get_object_or_404`` return Django's standard 404 response.
See :doc:`/content/howto/customize-error-pages` for customising what the visitor sees.

.. code-block:: jinja
   :caption: notes/pages/notes/[id]/edit/template.djx

   <h2>Edit {{ note.title }}</h2>
   {% form @action="update_note" %}
     <label>Title {{ form.title }}</label>
     <label>Body {{ form.body }}</label>
     <button type="submit">Save</button>
     <a href="{% url 'next:page_notes_id' id=note.id %}">Cancel</a>
   {% endform %}

The ``{% form %}`` tag builds and binds the form for the named action itself.
The ``form`` variable inside the block is the form for ``update_note``, pre-filled because the ``form_class`` factory bound it to the note instance.

Add a link from the detail page.

.. code-block:: jinja
   :caption: notes/pages/notes/[id]/template.djx

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
   :caption: notes/pages/notes/[id]/template.djx

   <article>
     <h2>{{ note.title }}</h2>
     {% if note.body %}<p>{{ note.body }}</p>{% endif %}
     <small>{{ note.created_at|date:"Y-m-d H:i" }}</small>
     <p>
       <a href="{% url 'next:page_notes_id_edit' id=note.id %}">Edit</a>
       {% form @action="delete_note" %}
         <input type="hidden" name="confirm" value="on">
         <button type="submit" class="button-danger">Delete</button>
       {% endform %}
     </p>
   </article>

The rendered form carries several hidden inputs from different sources.
``confirm`` is a real field on ``DeleteNoteForm``, so the template posts it explicitly.
The ``{% form %}`` tag emits the framework fields shown below.
``csrfmiddlewaretoken`` carries the CSRF token, ``_next_form_page`` identifies the origin page, ``_next_form_origin`` records the request path, and ``_url_param_id`` echoes the captured URL ``id``.
The ``_url_param_id`` field lets the action handler resolve ``DUrl["id", int]`` without any extra argument on the tag.
Add the action handler to the detail page.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.forms import DeleteNoteForm
   from notes.models import Note
   from next.forms import action
   from next.pages import context
   from next.urls import DUrl

   @context("note")
   def fetch_note(note_id: DUrl["id", int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

   @action("delete_note", form_class=DeleteNoteForm)
   def delete_note(note_id: DUrl["id", int], form: DeleteNoteForm) -> HttpResponseRedirect:
       get_object_or_404(Note, pk=note_id).delete()
       return HttpResponseRedirect(reverse("next:page_"))

Submit the delete button on a note and the detail page redirects to the index, which no longer lists that note.

How Re-render Works
~~~~~~~~~~~~~~~~~~~

A failing validation re-renders the origin page rather than producing an error page.
The framework keeps a per-request cache that memoises ``Depends("name")`` callables across the initial render and any subsequent re-render in the same request, and the re-render reads from it instead of recomputing.
See :doc:`/content/topics/dependency-injection` for the full cache model.
It runs the same context functions, so the surrounding page content stays consistent.
See :doc:`/content/topics/forms/validation-rerender` for the full re-render contract.

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
         component.djx
         component.py
         component.css
         component.js
     pages/
       layout.djx
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
Every action goes through a typed action handler that receives the validated form and any DI markers it asks for.

Common Pitfalls
---------------

Action fires but the page reloads with an empty form.
   Check that the handler returns an ``HttpResponseRedirect`` on success.
   A handler that returns ``None`` renders the page again from scratch.

CSRF token missing.
   ``{% form @action="..." %}`` injects the token automatically, but only on POST forms.
   Plain ``<form method="post">`` markup without the tag still needs ``{% csrf_token %}``.

Edit form does not show the existing data.
   The ``{% form %}`` tag builds the form for the named action, so a plain ``@context("form")`` value never reaches it.
   Pass a ``form_class`` factory to ``@action`` that resolves the URL ``id`` and returns the form class with the bound ``instance``.

See :doc:`/content/faq/troubleshooting` for the full catalog of errors and fixes.

Next Steps
----------

The application works end to end.
The next part shows how to test the pages and how to use the development server effectively.

.. seealso::

   :doc:`tutorial05` writes end-to-end tests with ``NextClient`` and ``SignalRecorder``.
   :doc:`/content/topics/forms/index` covers actions, formsets, and re-render mechanics in depth.
   :doc:`/content/topics/forms/validation-rerender` explains the dispatch pipeline.
