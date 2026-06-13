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
           abstract = True

   NoteFormSet = formset_factory(NoteRowForm, extra=3, can_delete=True)

``abstract = True`` matters here.
Without it, subclassing ``ModelForm`` would register ``NoteRowForm`` as the standalone action ``note_row_form`` through ``__init_subclass__``.
That is a live endpoint that saves a single row through the default ``on_valid``, even though only the formset action is intended.
The flag suppresses that registration, and ``formset_factory`` still builds the formset from the abstract class as usual.
See :ref:`Preventing Registration <topics-forms-actions-abstract>` for the ``Meta.abstract`` semantics.

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

Passing a formset class directly to ``@action``'s ``form_class`` is accepted at decoration time but fails at request time, because the dispatcher calls ``get_initial`` on a directly passed class and Django formset classes have none.
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
