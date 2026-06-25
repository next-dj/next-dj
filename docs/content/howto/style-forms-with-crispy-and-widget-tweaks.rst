.. _howto-crispy-widget-tweaks:

Style Forms With crispy-forms and widget-tweaks
===============================================

Problem
-------

You want Bootstrap-grade form markup inside ``{% form %}`` without hand-writing the HTML for every field.

Solution
--------

The ``{% form %}`` tag publishes an ordinary bound form under the ``form`` variable, so every renderer that consumes a Django form works unchanged.
Render the whole form through the ``|crispy`` filter, or restyle single fields with django-widget-tweaks filters.
No compatibility code is involved on either side.
Verified with django-crispy-forms 2.5 and later plus the crispy-bootstrap5 template pack, and django-widget-tweaks 1.5 and later, on Django 5.2 through 6.0.

Walkthrough
-----------

Install the Packages
~~~~~~~~~~~~~~~~~~~~

Add the apps and pick a template pack.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       # ...
       "crispy_forms",
       "crispy_bootstrap5",
       "widget_tweaks",
   ]

   CRISPY_ALLOWED_TEMPLATE_PACKS = "bootstrap5"
   CRISPY_TEMPLATE_PACK = "bootstrap5"

Render Through the ``|crispy`` Filter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The filter is the zero-configuration path.
It renders the fields with the template pack's markup and never emits a ``<form>`` element of its own, so it composes with ``{% form %}`` without any helper.

.. code-block:: python
   :caption: contact/pages/contact/page.py

   from django import forms
   from django.http import HttpRequest, HttpResponseRedirect

   import next.forms

   class ContactForm(next.forms.Form):
       name = forms.CharField(max_length=100)
       email = forms.EmailField()

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           return HttpResponseRedirect("/contact/thanks/")

.. code-block:: jinja
   :caption: contact/pages/contact/template.djx

   {% load crispy_forms_tags %}
   {% form "contact_form" %}
     {{ form|crispy }}
     <button type="submit">Send</button>
   {% endform %}

The output carries the Bootstrap 5 markup (``div_id_*`` wrappers, ``form-control`` classes) with exactly one ``<form>`` element and exactly one CSRF token, both owned by the tag.

Render Through the ``{% crispy %}`` Tag
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``{% crispy %}`` tag drives the layout from a ``FormHelper``, and a helper defaults to rendering its own ``<form>`` element and its own CSRF node.
Inside ``{% form %}`` both must be switched off.

.. code-block:: python
   :caption: contact/pages/contact/page.py

   from crispy_forms.helper import FormHelper

   class ContactForm(next.forms.Form):
       name = forms.CharField(max_length=100)
       email = forms.EmailField()

       @property
       def helper(self) -> FormHelper:
           helper = FormHelper()
           helper.form_tag = False
           helper.disable_csrf = True
           return helper

.. code-block:: jinja
   :caption: contact/pages/contact/template.djx

   {% load crispy_forms_tags %}
   {% form "contact_form" %}
     {% crispy form %}
     <button type="submit">Send</button>
   {% endform %}

Both helper lines are required, and they cover two different leaks.

``helper.form_tag = False``
   ``{% form %}`` already owns the ``<form>`` element, its ``action``, and the CSRF token.
   Without this line crispy nests a second ``<form>`` inside the first, which breaks silently until the submit posts to the wrong place.

``helper.disable_csrf = True``
   The crispy form template gates its CSRF node on the form method and ``disable_csrf``, not on ``form_tag``, so crispy still renders the node when ``form_tag`` is ``False``.
   A next.dj page renders without a ``csrf_token`` context variable, because the ``{% form %}`` tag injects the token itself, so the orphan node emits an empty string plus a ``UserWarning``.
   In a template rendered through a ``RequestContext`` it would emit a duplicate hidden input instead.

Define the helper as a ``@property`` rather than assigning it in ``__init__``.
The constructor of a registered form participates in the dispatch pipeline, and the property keeps its signature untouched and adds no per-instance state.

Restyle Single Fields With widget-tweaks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When the markup is hand-written and only the widget attributes need adjusting, the widget-tweaks filters apply to the pushed ``form`` variable directly.

.. code-block:: jinja
   :caption: contact/pages/contact/template.djx

   {% load widget_tweaks %}
   {% form "contact_form" %}
     {{ form.name|add_class:"form-control"|attr:"placeholder:Your name" }}
     {{ form.name.errors }}
     <button type="submit">Send</button>
   {% endform %}

Validation Re-Render
~~~~~~~~~~~~~~~~~~~~

An invalid submission re-renders the origin page with the bound failing form in place of ``form``, see :doc:`/content/topics/forms/validation-rerender`.
The styled markup survives that round trip.
Crispy renders the bound errors with the pack's error markup (``is-invalid`` and ``invalid-feedback`` under Bootstrap 5), and the widget-tweaks classes and attributes stay on the field next to its error list.

Verification
------------

Render the page and inspect the source.
The output contains exactly one ``<form>`` element and exactly one ``csrfmiddlewaretoken`` input.
Submit an invalid value.
The page re-renders with the crispy error markup, and the entered values stay in the fields.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/templates` for the ``{% form %}`` tag contract.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
   :doc:`drive-form-actions-with-htmx` for submitting these forms over htmx.
