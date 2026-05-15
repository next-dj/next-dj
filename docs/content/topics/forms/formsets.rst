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

The ``next.forms.Form`` and ``next.forms.ModelForm`` mixins apply to each row form inside the formset.
Use Django's standard factory functions to build the formset class.

Registering a Formset Action
----------------------------

Pass the formset class as ``form_class``.

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

.. code-block:: python
   :caption: notes/routes/notes/bulk/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse

   from next.forms import action

   from notes.forms import NoteFormSet


   @action("bulk_create_notes", form_class=NoteFormSet)
   def bulk_create_notes(form: NoteFormSet) -> HttpResponseRedirect:
       for row in form:
           if row.cleaned_data and not row.cleaned_data.get("DELETE"):
               row.instance.save()
       return HttpResponseRedirect(reverse("next:page_"))

Rendering the Formset
---------------------

Use the standard ``{% form %}`` tag.
The block body iterates the formset and renders each row.

.. code-block:: jinja
   :caption: notes/routes/notes/bulk/template.djx

   {% form @action="bulk_create_notes" method="post" %}
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

A formset that allows extra rows often comes with ``empty_permitted=True`` rows that have no instance.
The framework helper drops those initial values so untouched rows pass validation without producing spurious errors.

.. code-block:: python
   :caption: notes/forms.py

   from next.forms.formsets import cleanup_extra_initial


   def build_formset(queryset) -> NoteFormSet:
       formset = NoteFormSet(queryset=queryset)
       cleanup_extra_initial(formset)
       return formset

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
   :caption: notes/routes/notes/edit-all/page.py

   from django.http import HttpResponseRedirect
   from django.urls import reverse

   from next.forms import action
   from next.pages import context

   from notes.forms import NoteEditFormSet
   from notes.models import Note


   @context("form")
   def edit_formset() -> NoteEditFormSet:
       formset = NoteEditFormSet(queryset=Note.objects.all())
       return formset


   @action("edit_all_notes", form_class=NoteEditFormSet)
   def edit_all_notes(form: NoteEditFormSet) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(reverse("next:page_"))

The ``@context("form")`` callable publishes a bound formset to the template.
The handler receives the same formset for save.

Common Patterns
---------------

Add Form Button
~~~~~~~~~~~~~~~

Pair the formset with client side JS that clones the empty extra row.
The framework processes whatever the management form reports.

Partial Save
~~~~~~~~~~~~

Save only the valid rows by iterating ``form.cleaned_data`` and skipping rows with ``DELETE`` true or empty payloads.

Inline Formset Through DI
~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``inlineformset_factory`` for parent and child relationships.
The same registration pattern applies, the handler reads ``form.instance`` for the parent and iterates the formset for children.

Validation Failure
------------------

A failing validation re-renders the origin page with the bound formset in scope.
Field errors render on each row through ``row.errors`` and non field errors render through ``form.non_form_errors``.

See Also
--------

.. seealso::

   :doc:`modelforms` for single instance edit pages.
   :doc:`validation-rerender` for the re-render pipeline.
   :doc:`/content/howto/use-formsets` for a recipe.
   :doc:`/content/ref/forms` for the public API.
