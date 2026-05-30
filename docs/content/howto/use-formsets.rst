.. _howto-formsets:

Use Formsets
============

Problem
-------

You want a single page that submits several form rows at once.

Solution
--------

Build the formset with Django's :doc:`formset_factory <django:topics/forms/formsets>`, register an action that takes the formset as its ``form`` parameter, and render every row in the template.

Walkthrough
-----------

Define the row form and the formset.

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

Subclassing ``ModelForm`` registers ``NoteRowForm`` as the action ``note_row_form`` through ``__init_subclass__``, even though only the formset factory action is intended.
Render the formset through ``bulk_create`` and do not target ``{% form 'note_row_form' %}``, which would post a single row rather than the formset.

Register the action.

.. code-block:: python
   :caption: notes/pages/notes/bulk/page.py

   from django.forms.formsets import BaseFormSet
   from django.http import HttpResponseRedirect
   from django.urls import reverse
   from next.forms import action
   from notes.forms import NoteFormSet

   def build_bulk_formset() -> tuple[type[BaseFormSet], dict]:
       return NoteFormSet, {"prefix": "notes"}

   @action("bulk_create", form_class=build_bulk_formset)
   def bulk_create(form: NoteFormSet) -> HttpResponseRedirect:
       for row in form:
           if row.cleaned_data and not row.cleaned_data.get("DELETE"):
               row.save()
       return HttpResponseRedirect(reverse("next:page_"))

Passing a formset class directly to ``@action``'s ``form_class`` raises ``TypeError`` at decoration time, because the ``@action`` type-guard rejects any class.
Register a factory callable that returns a ``(FormSetClass, init_kwargs)`` tuple instead.
The ``init_kwargs`` reach the formset constructor, and a non-empty dict makes the dispatcher skip the ``get_initial`` step.
A formset has no ``get_initial``, so the ``init_kwargs`` must be non-empty even if they only set the ``prefix``.

The ``page_{path}`` URL name follows the file-router naming convention, see :doc:`/content/topics/file-router`.

Render the formset.

.. code-block:: jinja
   :caption: notes/pages/notes/bulk/template.djx

   {% form "bulk_create" %}
     {{ form.management_form }}
     {% for row in form %}
       <fieldset>
         <legend>Row {{ forloop.counter }}</legend>
         {{ row.title }}
         {{ row.body }}
       </fieldset>
     {% endfor %}
     <button type="submit">Save all</button>
   {% endform %}

Always render ``{{ form.management_form }}`` before the row loop.

Clean Up Empty Rows
-------------------

A formset with ``extra=3`` ships three blank rows.
When ``initial`` data is provided alongside those extra rows, Django pre-populates the blank rows with the initial values.
A user who leaves those rows untouched submits data that appears empty but carries hidden values, triggering validation errors.
Use ``cleanup_extra_initial`` to clear initial values from blank extra rows before the formset is rendered.

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

   @context("bulk_create")
   def bulk_create_form() -> SimpleNamespace:
       return SimpleNamespace(form=build_formset([{"title": "Draft"}]))

The ``{% form %}`` tag looks up a context variable named after the action and reads its ``.form`` attribute.
A regular form action satisfies this through its own ``get_initial``, but a formset has no ``get_initial``, so the ``@context`` callable must publish the value itself.
The callable name must match the action name, and the returned object must expose ``.form``, hence the ``SimpleNamespace(form=...)`` wrapper.
Returning the bare formset, or publishing it under a different key, leaves ``form`` as ``None`` in the template.

Verification
------------

Submit the formset with two filled rows and one blank row.
The handler saves the two filled rows and skips the blank one.
A row that fails validation re-renders with errors on that row only.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/formsets` for the topic guide.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
