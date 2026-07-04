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
   from django.http import HttpRequest, HttpResponseRedirect

   from next.forms import action, redirect_to_origin
   from notes.forms import NoteFormSet

   def build_bulk_formset() -> tuple[type[BaseFormSet], dict]:
       return NoteFormSet, {"prefix": "notes"}

   @action("bulk_create", form_class=build_bulk_formset)
   def bulk_create(request: HttpRequest, form: NoteFormSet) -> HttpResponseRedirect:
       for row in form:
           if row.cleaned_data and not row.cleaned_data.get("DELETE"):
               row.save()
       return redirect_to_origin(request)

Passing a formset class directly to ``@action``'s ``form_class`` is accepted at decoration time but fails at request time.
The dispatcher calls ``get_initial`` on a directly passed class, and Django formset classes have none.
Register a factory callable that returns a ``(FormSetClass, init_kwargs)`` tuple instead.
The ``init_kwargs`` reach the formset constructor, and a non-empty dict makes the dispatcher skip the ``get_initial`` step.
A formset has no ``get_initial``, so the ``init_kwargs`` must be non-empty even if they only set the ``prefix``.

``redirect_to_origin`` reads the posted origin path, so a successful submission lands back on the bulk page.

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
         {{ row.DELETE }}
       </fieldset>
     {% endfor %}
     <button type="submit">Save all</button>
   {% endform %}

Always render ``{{ form.management_form }}`` before the row loop.
``can_delete=True`` adds the ``DELETE`` checkbox to every row, and the handler skips the rows the user marked, so the template renders it alongside the fields.

Clean Up Empty Rows
-------------------

A formset with ``extra=3`` ships three blank rows.
Field-level initials, declared on the row form or inherited from model field defaults, pre-populate those blank rows.
An untouched pre-filled row no longer looks empty to Django, so it validates as a partial submission and triggers errors.
Use ``cleanup_extra_initial`` to clear initial values from blank extra rows before the formset is rendered.

.. code-block:: python
   :caption: notes/pages/notes/bulk/page.py

   from types import SimpleNamespace

   from next.forms import cleanup_extra_initial
   from next.pages import context
   from notes.forms import NoteFormSet

   @context("bulk_create")
   def bulk_create_form() -> SimpleNamespace:
       formset = NoteFormSet(prefix="notes")
       cleanup_extra_initial(formset)
       return SimpleNamespace(form=formset)

The ``{% form %}`` tag first looks up a context variable named after the action and reads its ``.form`` attribute.
When the variable is absent the tag builds the namespace itself from the registered factory, so a plain render needs no ``@context`` at all.
Publish the namespace only to customise construction, here to clean the field-level initials off the blank extra rows.
The context key passed to ``@context`` must match the action name, and the returned object must expose ``.form``, hence the ``SimpleNamespace(form=...)`` wrapper.

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
