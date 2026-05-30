.. _topics-forms-plain-forms:

Plain Forms
===========

A plain :doc:`Form <django:topics/forms/index>` collects and validates input without a Django model behind it.
It has no ``Meta.model``, no automatic ``save()``, and no instance loading.
The form validates ``cleaned_data`` and hands it to ``on_valid``, where the page decides what to do with it.

.. contents::
   :local:
   :depth: 2

When To Reach For One
---------------------

Use a plain ``Form`` when the submission does not map to a single model write.

Filter and search forms read a value and redirect with it.
Voting and bulk-operation forms run a targeted query or update across many rows.
None of these fit the create-one-row shape that :doc:`modelforms` covers, so a plain ``Form`` is the right base.

Registration
------------

Subclassing ``next.forms.Form`` registers the class and derives its action name and scope, exactly like a ``ModelForm``.
See :doc:`actions` for the registration rules.

.. code-block:: python
   :caption: obs/forms.py — auto-registered as ``window_filter_form`` (shared)

   from django import forms as django_forms

   from next.forms import Form

   class WindowFilterForm(Form):
       window = django_forms.ChoiceField(choices=WINDOW_CHOICES)

A form declared in ``forms.py`` takes ``shared`` scope and is reachable from any template by its derived name.
See :doc:`actions` for the full scope rules.

.. code-block:: jinja
   :caption: template.djx

   {% form "window_filter_form" %}
     {{ form.window }}
     <button type="submit">Apply</button>
   {% endform %}

Handling Submissions
--------------------

The default ``on_valid`` on a plain ``Form`` calls ``redirect_to_origin(request)`` and returns.

Override ``on_valid`` when the submission needs a different redirect or its own logic.
A filter form, for example, redirects with the picked value on the query string.

.. code-block:: python
   :caption: obs/forms.py — redirect with the chosen window

   from django.http import HttpRequest, HttpResponseRedirect

   class WindowFilterForm(Form):
       window = django_forms.ChoiceField(choices=WINDOW_CHOICES)

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           chosen = self.cleaned_data["window"]
           return HttpResponseRedirect(f"/stats/?window={chosen}")

The method reads ``self.cleaned_data`` directly.
There is no model to save, so the page owns every write.

.. code-block:: python
   :caption: flags/panels/admin/page.py — bulk update across many rows

   from django import forms
   from django.http import HttpRequest, HttpResponseRedirect

   from next.forms import Form

   class BulkToggleForm(Form):
       enabled_names = forms.MultipleChoiceField(required=False)

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           enabled_names = set(self.cleaned_data["enabled_names"])
           for flag in Flag.objects.all():
               should_be_on = flag.name in enabled_names
               if flag.enabled != should_be_on:
                   flag.enabled = should_be_on
                   flag.save(update_fields=["enabled", "updated_at"])
           return HttpResponseRedirect("/admin/")

Dynamic Choices
---------------

Populate choices in ``__init__`` when they depend on the database or the request.
Call ``super().__init__`` first, then rewrite the field's ``choices`` or ``queryset``.

.. code-block:: python
   :caption: flags/panels/admin/page.py — choices from current flag names

   from django import forms

   from next.forms import Form

   class BulkToggleForm(Form):
       enabled_names = forms.MultipleChoiceField(required=False)

       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           self.fields["enabled_names"].choices = [
               (name, name) for name in Flag.objects.values_list("name", flat=True)
           ]

The same pattern narrows a ``ModelChoiceField`` queryset to the submitted parent so Django rejects forged primary keys at field-validation time.

.. code-block:: python
   :caption: polls/forms.py — narrow the queryset on binding

   from django import forms as django_forms

   from next.forms import Form

   class VoteForm(Form):
       poll = django_forms.ModelChoiceField(queryset=Poll.objects.all())
       choice = django_forms.ModelChoiceField(queryset=Choice.objects.none())

       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           poll_pk = self.data.get(self.add_prefix("poll"))
           if poll_pk:
               self.fields["choice"].queryset = Choice.objects.filter(poll_id=poll_pk)

See Also
--------

.. seealso::

   :doc:`modelforms` for forms backed by a Django model.
   :doc:`actions` for auto-registration, name derivation, and scope.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`Django Forms <django:topics/forms/index>` for the underlying field and validation API.
