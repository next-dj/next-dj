.. _intro-tutorial04:

Forms and actions
=================

Goal
----

This part wires create, edit, and delete flows for notes through the form and action subsystem.
By the end the index has a create form, each detail page has edit and delete buttons, and every submission lands in a typed action handler that the framework dispatches automatically.
The delete action also carries an access guard, because an action endpoint is reachable without the page that renders its form.

Prerequisites
-------------

You have finished :doc:`tutorial03`.
The ``note_card`` component renders each note and the static collector emits its CSS and JS.

Action handlers resolve ``request``, the form, and URL segments through the same dependency injection used by pages, covered in :doc:`/content/topics/dependency-injection`.

Walkthrough
-----------

Declare the note forms
~~~~~~~~~~~~~~~~~~~~~~

Forms in next.dj are Django forms with one extra base class.
Create ``notes/forms.py``.

.. code-block:: python
   :caption: notes/forms.py

   from notes.models import Note
   from next.forms import BooleanField, Form, ModelForm

   class CreateNoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

   class DeleteNoteForm(Form):
       confirm = BooleanField(required=True)

``next.forms.ModelForm`` and ``next.forms.Form`` are the framework form base classes.
Subclassing either one auto-registers the form as an :term:`action`.
The action name is derived from the class name in ``snake_case``.
``CreateNoteForm`` registers as ``create_note_form`` and ``DeleteNoteForm`` registers as ``delete_note_form``.

Override ``on_valid`` to run code after the form passes validation.
The default ``on_valid`` saves a ``ModelForm`` and redirects back to the page that rendered the form, the :term:`origin page`.
Common Django fields and widgets are re-exported from ``next.forms``, see :doc:`/content/ref/forms`.

Register context for the index page
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The index page publishes context values.
Update ``notes/pages/page.py``.
The ``inherit_context=True`` flag on the three layout-scope callables stays from :doc:`tutorial02` so each value still reaches every descendant page.

.. code-block:: python
   :caption: notes/pages/page.py

   from notes.models import Note
   from next import context

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

``CreateNoteForm`` is declared in ``notes/forms.py`` and registers automatically at startup via autodiscovery.
No manual import in ``page.py`` is needed.
See :doc:`/content/topics/forms/overview` for scope rules and autodiscovery.

Render the create form
~~~~~~~~~~~~~~~~~~~~~~

Add the form to the index template.
The ``{% form %}`` tag takes the auto-derived action name as a quoted string.

.. code-block:: jinja
   :caption: notes/pages/template.djx

   <section class="note-create">
     {% form "create_note_form" %}
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

Edit a note
~~~~~~~~~~~

Create a new page at ``notes/pages/notes/[id]/edit/``.
The action binds ``CreateNoteForm`` to the existing note through a ``form_class`` factory callable.
A factory callable is a plain function (not a form class) that returns a ``(FormClass, init_kwargs)`` tuple.
It receives the same DI-resolved parameters as any other callable, including URL parameters.

.. code-block:: python
   :caption: notes/pages/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.forms import CreateNoteForm
   from notes.models import Note
   from next import action, context
   from next.urls import DUrl

   @context("note")
   def fetch_note(note_id: DUrl["id", int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

   def note_edit_form(note_id: DUrl["id", int]) -> tuple[type[CreateNoteForm], dict]:
       note = get_object_or_404(Note, pk=note_id)
       return CreateNoteForm, {"instance": note}

   @action("update_note", form_class=note_edit_form)
   def update_note(form: CreateNoteForm) -> HttpResponseRedirect:
       note = form.save()
       return HttpResponseRedirect(reverse("next:page_notes_id", kwargs={"id": note.id}))

The reverse name ``next:page_notes_id`` assumes the untyped ``notes/[id]/`` directory used in this tutorial.
A typed segment such as ``notes/[int:id]/`` produces ``page_notes_int_id`` instead.
The factory passed to ``form_class`` is dependency-resolved at dispatch time, so it receives the captured URL ``id`` and returns the form class paired with the ``instance`` to bind.
The dispatcher builds and validates that bound form before it calls ``update_note``, so the handler only saves it.
An ``id`` that matches no note makes ``get_object_or_404`` return Django's standard 404 response.
See :doc:`/content/howto/customize-error-pages` for customising what the visitor sees.

A ``ModelForm`` can express the same edit binding declaratively through ``Meta.instance_from_url``, which loads the instance from a URL parameter without a factory.
This tutorial keeps the factory to show the general ``form_class`` mechanism.
See :doc:`/content/topics/forms/modelforms` for the declarative alternative.

.. code-block:: jinja
   :caption: notes/pages/notes/[id]/edit/template.djx

   <h2>Edit {{ note.title }}</h2>
   {% form "update_note" %}
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

Delete a note
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
       {% form "delete_note_form" %}
         <input type="hidden" name="confirm" value="on">
         <button type="submit" class="button-danger">Delete</button>
       {% endform %}
     </p>
   </article>

The rendered form carries several hidden inputs from different sources.
``confirm`` is a real field on ``DeleteNoteForm``, so the template posts it explicitly.
The ``{% form %}`` tag emits the framework fields itself.
``csrfmiddlewaretoken`` carries the CSRF token and ``_next_form_origin`` records the page URL, such as ``/notes/7/``.
The dispatcher resolves that path against the URLconf, which recovers the captured ``id`` through the URL converter.
The action handler therefore resolves ``DUrl["id", int]`` without any extra argument on the tag.

.. warning::

   ``_next_form_origin`` is submitted by the client like every other field.
   The dispatcher checks only that the value is a same-site path, never that the visitor is allowed to act on the page it names.
   Every URL segment recovered from the origin is untrusted input, so an action that mutates a row guards itself.

Add the delete handler to the detail page.
``DeleteNoteForm`` is declared in ``notes/forms.py`` and registers automatically at startup via autodiscovery.
The detail ``page.py`` only needs to add its own context.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.models import Note
   from next import context
   from next.urls import DUrl

   @context("note")
   def fetch_note(note_id: DUrl["id", int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

``DeleteNoteForm.on_valid`` handles the delete, and ``Meta.login_required`` guards it.
Update ``notes/forms.py`` to add the full import block, the guard, and the delete logic.
The complete file now looks like this.

.. code-block:: python
   :caption: notes/forms.py, complete file

   from django.http import HttpRequest, HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from notes.models import Note
   from next.forms import BooleanField, Form, ModelForm
   from next.urls import DUrl

   class CreateNoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

   class DeleteNoteForm(Form):
       confirm = BooleanField(required=True)

       class Meta:
           login_required = True

       def on_valid(self, request: HttpRequest, note_id: DUrl["id", int]):
           get_object_or_404(Note, pk=note_id).delete()
           return HttpResponseRedirect(reverse("next:page_"))

An action is dispatched at ``/_next/form/<uid>/``, outside the page URL space, so nothing on the detail page protects it.
``Meta.login_required`` is the declarative guard for that endpoint, and the dispatcher enforces it before it builds the form.
An anonymous POST is redirected to ``LOGIN_URL`` with ``next`` set to the posted origin, and ``on_valid`` never reaches the database.
Without the guard any visitor deletes the note named by the origin they post.

The static guard decides who may call the action.
A multi-user version of Notes also has to decide who owns the row, which is what the ``has_object_permission`` hook answers.
See :ref:`topics-forms-actions-guards` for the full guard contract and :doc:`/content/howto/enforce-object-level-permissions` for the owner check.

Sign in before deleting
~~~~~~~~~~~~~~~~~~~~~~~

The guard needs somewhere to send an anonymous visitor.
``django.contrib.auth`` is already installed from :doc:`install`, so its built-in views cover the login page.
Mount them above the file router and send a successful login back to the index.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("accounts/", include("django.contrib.auth.urls")),
       path("", include("next.urls")),
   ]

.. code-block:: python
   :caption: config/settings.py, one added line

   LOGIN_REDIRECT_URL = "/"

The login view renders ``registration/login.html``, which the application supplies through the ordinary Django template loader.

.. code-block:: jinja
   :caption: notes/templates/registration/login.html

   <form method="post">
     {% csrf_token %}
     {{ form.as_div }}
     <button type="submit">Sign in</button>
   </form>

Create an account and use it to sign in.

.. code-block:: bash
   :caption: shell

   uv run python manage.py createsuperuser

Open ``http://127.0.0.1:8000/accounts/login/`` and sign in with that account.
Submit the delete button on a note and the detail page redirects to the index, which no longer lists that note.
An anonymous visitor who posts the same form lands on the login page instead, and the note survives.

How re-render works
~~~~~~~~~~~~~~~~~~~

A failing validation re-renders the origin page rather than producing an error page.
The framework keeps a per-request cache that memoises dependency-injected values between the initial dispatch and the re-render, so the re-render reads from it instead of recomputing.
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
     templates/
       registration/
         login.html
     pages/
       layout.djx
       page.py
       template.djx
       _components/
         note_card/
           component.djx
           component.py
           component.css
           component.js
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
The delete action requires a signed-in user, and the create and edit actions are still open to anyone who reaches their endpoints.

Common pitfalls
---------------

Action fires but the page reloads with an empty form.
   Check that the handler returns an ``HttpResponseRedirect`` on success.
   A handler that returns ``None`` renders the page again from scratch.

CSRF token missing.
   ``{% form "..." %}`` injects the token automatically.
   Plain ``<form method="post">`` markup without the tag still needs ``{% csrf_token %}``.

Edit form does not show the existing data.
   The ``{% form %}`` tag builds the form for the named action, so a plain ``@context("form")`` value never reaches it.
   Pass a ``form_class`` factory callable to ``@action`` that resolves the URL ``id`` and returns the form class with the bound ``instance``.

Delete lands on the login page instead of deleting.
   ``DeleteNoteForm`` declares ``Meta.login_required``, so the dispatcher redirects an anonymous POST to ``LOGIN_URL``.
   Sign in at ``/accounts/login/`` and submit the form again.

See :doc:`/content/faq/troubleshooting` for the full catalog of errors and fixes.

Next steps
----------

The application works end to end.
The next part shows how to test the pages and how to use the development server effectively.

.. seealso::

   :doc:`tutorial05` writes end-to-end tests with ``NextClient`` and ``SignalRecorder``.
   :doc:`/content/topics/forms/index` covers actions, formsets, and re-render mechanics in depth.
   :doc:`/content/topics/forms/validation-rerender` explains the dispatch pipeline.
   :doc:`/content/security/overview` covers the access model behind the delete guard.
