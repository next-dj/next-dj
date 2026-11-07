.. _howto-formsets:

Use formsets
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

   from notes.models import Note

   from next.forms import ModelForm, formset_factory

   class NoteRowForm(ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")
           abstract = True

   NoteFormSet = formset_factory(NoteRowForm, extra=3, can_delete=True)

Register the action.

.. code-block:: python
   :caption: notes/pages/notes/bulk/page.py

   from django.forms.formsets import BaseFormSet
   from django.http import HttpRequest, HttpResponseRedirect
   from notes.forms import NoteFormSet

   from next import action
   from next.forms import redirect_to_origin

   def build_bulk_formset() -> tuple[type[BaseFormSet], dict]:
       return NoteFormSet, {"prefix": "notes"}

   @action("bulk_create", form_class=build_bulk_formset)
   def bulk_create(request: HttpRequest, form: NoteFormSet) -> HttpResponseRedirect:
       for row in form:
           if row.cleaned_data and not row.cleaned_data.get("DELETE"):
               row.save()
       return redirect_to_origin(request)

Mark the row form ``abstract`` so it registers no action of its own, and register the formset through a factory that returns a non-empty ``(FormSetClass, init_kwargs)`` tuple.
:doc:`/content/topics/forms/formsets` explains both rules.

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

Clean up empty rows
-------------------

Use ``cleanup_extra_initial`` to clear initial values from blank extra rows before the formset is rendered, see :doc:`/content/topics/forms/formsets`.

.. code-block:: python
   :caption: notes/pages/notes/bulk/page.py

   from types import SimpleNamespace

   from notes.forms import NoteFormSet

   from next import context
   from next.forms import cleanup_extra_initial

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

See also
--------

.. seealso::

   :doc:`/content/topics/forms/formsets` for the topic guide.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
