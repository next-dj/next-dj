.. _topics-forms-modelforms:

ModelForm Support
=================

A :doc:`ModelForm <django:topics/forms/modelforms>` adapts a Django model to a form.
next.dj supports ModelForms anywhere a plain ``Form`` works.
This page covers the ``next.forms.ModelForm`` base class, the ``get_initial`` hook that pre fills create and edit pages, and the patterns for routing instance lookups through the dependency injector.

.. contents::
   :local:
   :depth: 2

Base Class Setup
----------------

Subclass the framework's ``ModelForm`` base class.

.. code-block:: python
   :caption: notes/forms.py

   from next.forms import ModelForm
   from notes.models import Note

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

``next.forms.ModelForm`` adds the ``get_initial`` classmethod that ``next.forms.Form`` also adds for plain forms.
The dispatcher calls ``get_initial`` to compute the initial bound state of the form before binding the POST body.

get_initial
-----------

The framework calls ``get_initial`` at render time to compute the initial bound state of the form.
A ModelForm can return either a dict for fresh creation or an instance for editing.

.. code-block:: python
   :caption: notes/forms.py

   from typing import Any

   from django.http import Http404, HttpRequest
   from next.forms import ModelForm
   from notes.models import Note

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       @classmethod
       def get_initial(cls, request: HttpRequest, id: int | None = None) -> Note | dict[str, Any]:
           if id is None:
               return {}
           try:
               return Note.objects.get(pk=id)
           except Note.DoesNotExist as exc:
               raise Http404 from exc

The method runs through the dependency injector.
Add captured URL parameters as keyword arguments and the framework fills them automatically.

When the method returns an instance, the framework constructs the form with ``instance=...`` so the rendered fields show the existing values.
When it returns a dict, the framework constructs an unbound form with ``initial=...``.

Create Page
-----------

A create page renders the unbound form and saves it on submission.

.. code-block:: python
   :caption: notes/pages/notes/new/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from notes.forms import NoteForm

   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(reverse("next:page_"))

.. code-block:: jinja
   :caption: notes/pages/notes/new/template.djx

   {% form @action="create_note" %}
     {{ form.title }}
     {{ form.body }}
     <button type="submit">Create</button>
   {% endform %}

The ``get_initial`` method returns an empty dict, so the form renders as unbound.

Edit Page
---------

An edit page reuses the same form class and saves the bound instance.

.. code-block:: python
   :caption: notes/pages/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse
   from next.forms import action
   from next.pages import context
   from next.urls import DUrl
   from notes.forms import NoteForm
   from notes.models import Note

   @context("note")
   def fetch_note(note_id: DUrl["id", int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

   @action("update_note", form_class=NoteForm)
   def update_note(form: NoteForm, note_id: DUrl["id", int]) -> HttpResponseRedirect:
       form.instance = get_object_or_404(Note, pk=note_id)
       form.save()
       return HttpResponseRedirect(reverse("next:page_notes_id", kwargs={"id": note_id}))

The ``get_initial`` method receives ``id`` as a keyword argument because the URL captures it.
On render the form is constructed with the matching instance.
On submission the handler reattaches the instance before saving.

Captured Instance Through DI
----------------------------

A custom DI provider can centralise the instance lookup.

.. code-block:: python
   :caption: notes/providers.py

   from typing import get_args, get_origin
   from django.http import Http404
   from next.deps import DDependencyBase, RegisteredParameterProvider

   class DInstance[T](DDependencyBase[T]):
       __slots__ = ()

   class InstanceProvider(RegisteredParameterProvider):
       def can_handle(self, param, _context) -> bool:
           return get_origin(param.annotation) is DInstance

       def resolve(self, param, context):
           (model_cls,) = get_args(param.annotation)
           pk = context.url_kwargs["id"]
           try:
               return model_cls.objects.get(pk=pk)
           except model_cls.DoesNotExist as exc:
               raise Http404 from exc

The handler can now take the instance directly.

.. code-block:: python
   :caption: notes/pages/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from notes.forms import NoteForm
   from notes.models import Note
   from notes.providers import DInstance

   @action("update_note", form_class=NoteForm)
   def update_note(
       form: NoteForm,
       note: DInstance[Note],
   ) -> HttpResponseRedirect:
       form.instance = note
       form.save()
       return HttpResponseRedirect(reverse("next:page_notes_id", kwargs={"id": note.id}))

See :doc:`/content/topics/dependency-injection` for the marker mechanics.

Validation Failure
------------------

A failing validation re-renders the origin page with the bound form in scope.
For a ModelForm this means the user sees the values they typed plus any field errors.

The framework does not call ``save()`` when validation fails.
The handler runs only when ``form.is_valid()`` returns ``True``.

Setting an Audit Field
----------------------

A ModelForm carries no request-derived attributes.
The dispatcher resolves ``request`` into the handler signature, so set audit fields in the handler with ``form.save(commit=False)`` before the final save.

.. code-block:: python
   :caption: notes/pages/notes/new/page.py

   from django.http import HttpRequest, HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from notes.forms import NoteForm

   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm, request: HttpRequest) -> HttpResponseRedirect:
       note = form.save(commit=False)
       note.modified_by = request.user
       note.save()
       return HttpResponseRedirect(reverse("next:page_"))

``form.save(commit=False)`` returns the unsaved instance so the handler can attach the user before writing the row.
Call ``form.save_m2m()`` after ``note.save()`` when the model has many-to-many fields.

System Checks
-------------

The forms subsystem contributes the same ``next.E041`` collision check that flags two ``@action`` registrations sharing a name.
A handler that declares a ``form`` parameter still needs ``form_class`` on the decorator, otherwise no form is bound and the ``form`` parameter resolves to ``None`` at dispatch time.

Run ``uv run python manage.py check`` after every form definition change.

Common Patterns
---------------

Inline Create Form on the Listing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Render the create form on the listing page so the user can add a note without navigating.
A failed submission re-renders the listing with the bound form intact.

Separate Create and Edit Forms
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Define two ModelForm classes when create and edit need different fields.
Each action takes the appropriate form class.

Per User Initial Data
~~~~~~~~~~~~~~~~~~~~~

Use ``get_initial`` to pre fill fields with values from ``request.user`` such as the user name and email.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`formsets` for collections of model instances.
   :doc:`/content/howto/use-modelform-for-crud` for a step-by-step recipe.
   :doc:`/content/ref/forms` for the public API.
   :doc:`Django ModelForm <django:topics/forms/modelforms>` and :func:`~django.shortcuts.get_object_or_404` for the underlying Django behaviour.
