.. _topics-forms-plain-forms:

Plain forms
===========

A plain :doc:`Form <django:topics/forms/index>` collects and validates input without a Django model behind it.
It has no ``Meta.model``, no automatic ``save()``, and no instance loading.
The form validates ``cleaned_data`` and hands it to ``on_valid``, where the page decides what to do with it.

.. contents::
   :local:
   :depth: 2

When to reach for one
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

   import next.forms

   class WindowFilterForm(next.forms.Form):
       window = next.forms.ChoiceField(choices=WINDOW_CHOICES)

A form declared in ``forms.py`` takes ``shared`` scope and is reachable from any template by its derived name.
See :doc:`actions` for the full scope rules.

.. code-block:: jinja
   :caption: template.djx

   {% form "window_filter_form" %}
     {{ form.window }}
     <button type="submit">Apply</button>
   {% endform %}

Handling submissions
--------------------

The default ``on_valid`` on a plain ``Form`` redirects to ``Meta.success_url`` when declared, otherwise back to the origin page through ``redirect_to_origin(request)``.
See :ref:`topics-forms-actions-success` for the redirect contract.

Override ``on_valid`` when the submission needs a different redirect or its own logic.
A filter form, for example, redirects with the picked value on the query string.

.. code-block:: python
   :caption: obs/forms.py — redirect with the chosen window

   from django.http import HttpRequest, HttpResponseRedirect

   import next.forms

   class WindowFilterForm(next.forms.Form):
       window = next.forms.ChoiceField(choices=WINDOW_CHOICES)

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           chosen = self.cleaned_data["window"]
           return HttpResponseRedirect(f"/stats/?window={chosen}")

The method reads ``self.cleaned_data`` directly.
There is no model to save, so the page owns every write.

The bulk form below declares ``enabled_names`` with an empty choice list, so a ``MultipleChoiceField`` rejects every submitted value until `Dynamic choices`_ fills the list in ``__init__``.

.. code-block:: python
   :caption: flags/panels/admin/page.py — bulk update across many rows

   from django.http import HttpRequest, HttpResponseRedirect

   import next.forms

   class BulkToggleForm(next.forms.Form):
       enabled_names = next.forms.MultipleChoiceField(required=False)

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           enabled_names = set(self.cleaned_data["enabled_names"])
           for flag in Flag.objects.all():
               should_be_on = flag.name in enabled_names
               if flag.enabled != should_be_on:
                   flag.enabled = should_be_on
                   flag.save(update_fields=["enabled", "updated_at"])
           return HttpResponseRedirect("/admin/")

Round-tripping the filter
-------------------------

A filter form owns both halves of a round trip.
The redirect above puts the picked value on the query string, and the page reads it back on the next GET through ``DQuery[str]``.

.. code-block:: python
   :caption: obs/stats/page.py — read the window currently in force

   from next import context
   from next.urls import DQuery

   @context("current_window")
   def current_window(window: DQuery[str] = "7d") -> str:
       return window

A ``get_initial`` classmethod pre-selects the same value on the form itself.
Without it the control resets to the first choice on every render and shows a filter the page is not applying.

.. code-block:: python
   :caption: obs/forms.py — pre-select the current window

   import next.forms
   from next.urls import DQuery

   class WindowFilterForm(next.forms.Form):
       window = next.forms.ChoiceField(choices=WINDOW_CHOICES)

       @classmethod
       def get_initial(cls, window: DQuery[str] = "7d") -> dict:
           return {"window": window}

The ``{% form %}`` tag writes the rendering URL with its query string into ``_next_form_origin``, so a handler that ends in ``redirect_to_origin`` returns the visitor to the filtered view rather than the bare page.

Success feedback on a plain form
--------------------------------

``Meta.success_url`` names the redirect target and ``Meta.success_message`` queues a Django message, so a form that only needs a destination and a confirmation writes no ``on_valid`` at all.
See :ref:`topics-forms-actions-success` for the message interpolation contract and the redirect precedence.

Dynamic choices
---------------

Populate choices in ``__init__`` when they depend on the database or the request.
Call ``super().__init__`` first, then rewrite the field's ``choices`` or ``queryset``.

.. code-block:: python
   :caption: flags/panels/admin/page.py — choices from current flag names

   import next.forms

   class BulkToggleForm(next.forms.Form):
       enabled_names = next.forms.MultipleChoiceField(required=False)

       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           self.fields["enabled_names"].choices = [
               (name, name) for name in Flag.objects.values_list("name", flat=True)
           ]

The same pattern narrows a ``ModelChoiceField`` queryset to the submitted parent so Django rejects forged primary keys at field-validation time.

.. code-block:: python
   :caption: polls/forms.py — narrow the queryset on binding

   import next.forms

   class VoteForm(next.forms.Form):
       poll = next.forms.ModelChoiceField(queryset=Poll.objects.all())
       choice = next.forms.ModelChoiceField(queryset=Choice.objects.none())

       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           poll_pk = self.data.get(self.add_prefix("poll"))
           if poll_pk:
               self.fields["choice"].queryset = Choice.objects.filter(poll_id=poll_pk)

See also
--------

.. seealso::

   :doc:`modelforms` for forms backed by a Django model.
   :doc:`actions` for auto-registration, name derivation, and scope.
   :doc:`templates` for the ``{% form %}`` tag.
   :doc:`Django Forms <django:topics/forms/index>` for the underlying field and validation API.
