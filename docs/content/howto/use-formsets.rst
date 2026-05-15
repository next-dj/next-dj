.. _howto-formsets:

Use Formsets
============

Problem
-------

You want a single page that submits several form rows at once.

Solution
--------

Build the formset with ``formset_factory``, register an action that takes the formset as its ``form`` parameter, and render every row in the template.

Walkthrough
-----------

Define the row form and the formset.

.. code-block:: python
   :caption: notes/forms.py

   from django import forms
   from django.forms import formset_factory

   from next.forms import Form

   from notes.models import Note


   class NoteRowForm(Form, forms.ModelForm):
       class Meta:
           model = Note
           fields = ("title", "body")


   NoteFormSet = formset_factory(NoteRowForm, extra=3, can_delete=True)

Register the action.

.. code-block:: python
   :caption: notes/routes/notes/bulk/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse

   from next.forms import action

   from notes.forms import NoteFormSet


   @action("bulk_create", form_class=NoteFormSet)
   def bulk_create(form: NoteFormSet) -> HttpResponseRedirect:
       for row in form:
           if row.cleaned_data and not row.cleaned_data.get("DELETE"):
               row.instance.save()
       return HttpResponseRedirect(reverse("next:page_"))

Render the formset.

.. code-block:: jinja
   :caption: notes/routes/notes/bulk/template.djx

   {% form @action="bulk_create" method="post" %}
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
Use ``cleanup_extra_initial`` to strip default values from untouched rows so the user does not face spurious errors.

.. code-block:: python
   :caption: notes/forms.py

   from next.forms.formsets import cleanup_extra_initial


   def build_formset(queryset) -> NoteFormSet:
       formset = NoteFormSet(queryset=queryset)
       cleanup_extra_initial(formset)
       return formset

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
