.. _topics-forms-formsets:

Formsets
========

A formset bundles several forms that submit together as a unit.
Django provides ``formset_factory`` and ``modelformset_factory`` to construct them.
next.dj dispatches formset submissions through the same action pipeline that handles single forms.
This page covers the patterns for registering a formset action, rendering rows in templates, and using the ``cleanup_extra_initial`` helper.

.. contents::
   :local:
   :depth: 2

Overview
--------

A formset action looks like any other action but takes the entire formset as a single ``form`` parameter.
The ``form`` value in the handler is the bound formset, not an individual form.

The ``next.forms.Form`` and ``next.forms.ModelForm`` base classes apply to each row form inside the formset.
Use Django's standard :doc:`factory functions <django:topics/forms/formsets>` to build the formset class.

Registering a Formset Action
----------------------------

Pass the formset class as ``form_class``.

.. code-block:: python
   :caption: notes/forms.py

   from django.forms import formset_factory
   from next.forms import ModelForm
   from notes.models import Note

   class NoteRowForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

   NoteFormSet = formset_factory(NoteRowForm, extra=3, can_delete=True)

.. code-block:: python
   :caption: notes/pages/notes/bulk/page.py

   from django.forms.formsets import BaseFormSet
   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from notes.forms import NoteFormSet

   def build_bulk_formset() -> tuple[type[BaseFormSet], dict]:
       return NoteFormSet, {"initial": [{"title": "Draft"}]}

   @action("bulk_create_notes", form_class=build_bulk_formset)
   def bulk_create_notes(form: NoteFormSet) -> HttpResponseRedirect:
       for row in form:
           if row.cleaned_data and not row.cleaned_data.get("DELETE"):
               row.save()
       return HttpResponseRedirect(reverse("next:page_"))

Passing a formset class directly to ``form_class`` raises ``TypeError`` at dispatch time because the dispatcher expects a ``get_initial`` method on the form class.
Register a factory callable that returns a ``(FormSetClass, init_kwargs)`` tuple instead.
The ``init_kwargs`` reach the formset constructor and the dispatcher skips the ``get_initial`` step.

The ``page_{path}`` URL name follows the file-router naming convention, see :doc:`/content/topics/file-router`.

Rendering the Formset
---------------------

Use the standard ``{% form %}`` tag.
The block body iterates the formset and renders each row.

.. code-block:: jinja
   :caption: notes/pages/notes/bulk/template.djx

   {% form "bulk_create_notes" %}
     {{ form.management_form }}
     {% for row in form %}
       <fieldset>
         <legend>Row {{ forloop.counter }}</legend>
         {{ row.title }}
         {{ row.body }}
         {% if row.errors %}
           <ul class="errors">
             {% for field, errors in row.errors.items %}
               {% for error in errors %}<li>{{ field }} {{ error }}</li>{% endfor %}
             {% endfor %}
           </ul>
         {% endif %}
       </fieldset>
     {% endfor %}
     <button type="submit">Save all</button>
   {% endform %}

Always render ``{{ form.management_form }}`` inside the form.
Without it Django cannot reconstruct the formset on POST.

cleanup_extra_initial
---------------------

A formset that allows extra rows often comes with ``empty_permitted=True`` rows that have no saved instance behind them.
The framework helper drops those initial values so untouched rows pass validation without producing spurious errors.

Build the formset inside a ``@context`` callable named after the action and return a ``SimpleNamespace`` with a ``form`` attribute, the shape the ``{% form %}`` tag reads on the initial render.

.. code-block:: python
   :caption: notes/pages/notes/bulk/page.py

   from types import SimpleNamespace
   from next.forms import cleanup_extra_initial
   from next.pages import context
   from notes.forms import NoteFormSet

   def build_formset(initial: list[dict]) -> NoteFormSet:
       formset = NoteFormSet(initial=initial)
       cleanup_extra_initial(formset)
       return formset

   @context("bulk_create_notes")
   def bulk_create_notes_form() -> SimpleNamespace:
       return SimpleNamespace(form=build_formset([{"title": "Draft"}]))

The helper is idempotent.
Call it once after constructing the formset.

Edit Existing Rows
------------------

Use ``modelformset_factory`` for editing several existing instances.

.. code-block:: python
   :caption: notes/forms.py

   from django.forms import modelformset_factory
   from next.forms import ModelForm
   from notes.models import Note

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

   NoteEditFormSet = modelformset_factory(Note, form=NoteForm, extra=0, can_delete=True)

.. code-block:: python
   :caption: notes/pages/notes/edit-all/page.py

   from types import SimpleNamespace

   from django.forms.formsets import BaseFormSet
   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from next.pages import context
   from notes.forms import NoteEditFormSet
   from notes.models import Note

   @context("edit_all_notes")
   def edit_formset() -> SimpleNamespace:
       formset = NoteEditFormSet(queryset=Note.objects.all())
       return SimpleNamespace(form=formset)

   def build_edit_formset() -> tuple[type[BaseFormSet], dict]:
       return NoteEditFormSet, {"queryset": Note.objects.all()}

   @action("edit_all_notes", form_class=build_edit_formset)
   def edit_all_notes(form: NoteEditFormSet) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(reverse("next:page_"))

The ``@context("edit_all_notes")`` callable publishes a bound formset under the action-named key the ``{% form %}`` tag reads.
The handler receives the same formset for save.

Validation Failure
------------------

A failing validation re-renders the origin page with the bound formset in scope.
Field errors render on each row through ``row.errors`` and non field errors render through ``form.non_form_errors``.

Validating an Inline Formset
----------------------------

A parent form that owns an inline formset attaches the formset on construction and validates it inside ``clean``.
Raising ``ValidationError`` from ``clean`` routes the failure through the standard re-render pipeline.

Use a factory callable as ``form_class`` so the dispatcher binds the inline formset to the parent form before calling ``form.is_valid()``.
The factory returns ``(FormClass, init_kwargs)`` and the dispatcher passes those kwargs to the constructor.
``NoteForm.__init__`` rebinds ``row_formset`` to ``self.data`` with ``instance=self.instance`` so the formset validates against the same POST as the parent form.

.. code-block:: python
   :caption: notes/forms.py

   from django.forms import inlineformset_factory
   from next.forms import ModelForm
   from notes.models import Note, Row

   RowFormSet = inlineformset_factory(Note, Row, fields=("label",), extra=1)

   class NoteForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")

       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           self.row_formset = RowFormSet(
               data=self.data or None,
               instance=self.instance,
               prefix="rows",
           )

       def clean(self):
           cleaned = super().clean()
           if not self.row_formset.is_valid():
               self.add_error(None, "Fix the row errors below.")
           return cleaned

.. code-block:: python
   :caption: notes/pages/notes/[id]/edit/page.py

   from django.http import HttpResponseRedirect
   from django.shortcuts import get_object_or_404
   from next.forms import action
   from next.urls import DUrl
   from notes.forms import NoteForm
   from notes.models import Note

   def note_form_factory(note_id: DUrl["id", int]) -> tuple:
       note = get_object_or_404(Note, pk=note_id)
       return NoteForm, {"instance": note}

   @action("update_note", form_class=note_form_factory)
   def update_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       form.row_formset.save()
       return HttpResponseRedirect("/")

The parent page re-renders with both the parent and the row errors in scope on validation failure.

Common Patterns
---------------

Add Form Button
~~~~~~~~~~~~~~~

Pair the formset with client side JS that clones the empty extra row.
The framework processes whatever the management form reports.

Partial Save
~~~~~~~~~~~~

Save only the valid rows by iterating the formset and skipping rows whose ``cleaned_data`` is empty or carries a truthy ``DELETE``.

See Also
--------

.. seealso::

   :doc:`modelforms` for single instance edit pages.
   :doc:`validation-rerender` for the re-render pipeline.
   :doc:`/content/howto/use-formsets` for a recipe.
   :doc:`/content/ref/forms` for the public API.
   :doc:`Django formsets <django:topics/forms/formsets>` for ``formset_factory``, ``modelformset_factory``, and ``inlineformset_factory``.
