.. _topics-forms-modelforms:

ModelForm Support
=================

A ``ModelForm`` adapts a Django model to a form.
next.dj supports ModelForms anywhere a plain ``Form`` works.
This page covers the ``next.forms.ModelForm`` mixin, the ``get_initial`` hook that pre fills create and edit pages, and the patterns for routing instance lookups through the dependency injector.

.. contents::
   :local:
   :depth: 2

Mixin Setup
-----------

Combine the framework mixin with Django's ``ModelForm``.

.. code-block:: python
   :caption: notes/forms.py

   from django import forms

   from next.forms import ModelForm

   from notes.models import Note


   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

``next.forms.ModelForm`` provides the same identity tracking that ``next.forms.Form`` adds for plain forms.
The dispatcher needs that identity to re-render the origin page when validation fails.

get_initial
-----------

The framework calls ``get_initial`` at render time to compute the initial bound state of the form.
A ModelForm can return either a dict for fresh creation or an instance for editing.

.. code-block:: python
   :caption: notes/forms.py

   from django.http import HttpRequest

   from next.forms import ModelForm

   from notes.models import Note


   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       @classmethod
       def get_initial(cls, request: HttpRequest, id: int | None = None) -> Note | dict:
           if id is not None:
               try:
                   return Note.objects.get(pk=id)
               except Note.DoesNotExist:
                   return {}
           return {}

The method runs through the dependency injector.
Add captured URL parameters as keyword arguments and the framework fills them automatically.

When the method returns an instance, the framework constructs the form with ``instance=...`` so the rendered fields show the existing values.
When it returns a dict, the framework constructs an unbound form with ``initial=...``.

Create Page
-----------

A create page renders the unbound form and saves it on submission.

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

.. code-block:: jinja
   :caption: notes/routes/notes/new/template.djx

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
   :caption: notes/routes/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from django.urls import reverse

   from next.forms import action
   from next.pages import context
   from next.urls.markers import DUrl

   from notes.forms import NoteForm
   from notes.models import Note


   @context("note")
   def fetch_note(note_id: DUrl[int]) -> Note:
       return get_object_or_404(Note, pk=note_id)


   @action("update_note", form_class=NoteForm)
   def update_note(form: NoteForm, note_id: DUrl[int]) -> HttpResponseRedirect:
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

   from django.http import Http404
   from typing import get_args, get_origin

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
   :caption: notes/routes/notes/[id]/edit/page.py

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

ModelForm With Custom Save
--------------------------

Override ``save`` on the ModelForm when you need extra side effects.
The handler still calls ``form.save()`` and the override runs.

.. code-block:: python
   :caption: form with audit trail

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       def save(self, commit: bool = True) -> Note:
           instance = super().save(commit=False)
           instance.modified_by = self.request_user
           if commit:
               instance.save()
           return instance

The framework does not inject ``request_user`` into the form by itself.
Add a ``@context("form")`` that constructs the form with the user attached, or pass the user in the handler before calling ``save``.

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
