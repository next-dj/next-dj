.. _howto-htmx:

Drive Form Actions With htmx
============================

Problem
-------

You want a form to submit over htmx and swap only its own region, instead of a full page navigation.

Solution
--------

Pass ``hx-*`` attributes through the ``{% form %}`` tag.
The tag reserves only ``action``, ``method``, and the ``data-next-*`` prefix, so every htmx attribute lands on the ``<form>`` element unchanged.
Boost the form with ``hx-boost`` and carve its region out of the response with ``hx-select``.
Verified with django-htmx 1.19 and later on Django 4.2 through 6.0.

The dispatcher answers an invalid submission with the complete origin page, not a fragment, so ``hx-select`` carves the form region out of it.

Walkthrough
-----------

Boost the Form
~~~~~~~~~~~~~~

A boosted form submits to its own ``action`` attribute over AJAX, and the tag has already pointed ``action`` at the dispatch endpoint.

.. code-block:: jinja
   :caption: contact/pages/contact/template.djx

   {% form "contact_form" hx-boost="true" hx-select="#contact" hx-target="#contact" hx-swap="outerHTML" %}
     <div id="contact">
       {{ form.as_div }}
       <button type="submit">Send</button>
     </div>
   {% endform %}

Two flows leave this markup.

Invalid submission.
   The dispatcher re-renders the full origin page with HTTP 200 and the bound failing form.
   ``hx-select`` extracts ``#contact`` from that page and swaps it into the target, so the entered values and the field errors land in place.
   The ``hx-*`` attributes are part of the re-rendered markup, so the next submission behaves the same way.

Valid submission.
   The handler answers with a redirect, the request machinery follows it before htmx sees the response, and the selected fragment of the destination page lands in the target.

Post Explicitly With ``{% action_url %}``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``{% action_url %}`` tag returns the dispatch endpoint for an action name, resolved with the same page scoping as ``{% form %}``.
Assign it to a variable and pass it as ``hx-post`` when explicit htmx wiring is preferred over boosting.

.. code-block:: jinja
   :caption: contact/pages/contact/template.djx

   {% action_url "contact_form" as contact_endpoint %}
   {% form "contact_form" hx-post=contact_endpoint hx-select="#contact" hx-target="#contact" hx-swap="outerHTML" %}
     <div id="contact">
       {{ form.as_div }}
       <button type="submit">Send</button>
     </div>
   {% endform %}

htmx serialises the form on submit, so the hidden ``csrfmiddlewaretoken`` and ``_next_form_origin`` inputs that the tag emits travel with the request.
Keep ``hx-post`` on the ``<form>`` element for that reason.
An ``hx-post`` on an element outside the form would post without those fields and the dispatcher could not validate or re-render.

Address the Form From Scripts
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The opening tag carries ``data-next-action`` with the action UID, the registry identity that also names the dispatch URL.
Client-side code selects the form through that attribute instead of parsing the ``action`` URL.

.. code-block:: javascript
   :caption: selecting the form element

   const form = document.querySelector('form[data-next-action]');

Distinguish Re-Render From Redirect
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An invalid submission answers with the headers ``X-Next-Form: invalid`` and ``X-Next-Action: <uid>``.
A successful submission redirects, the redirect is followed transparently, and the final response carries neither header.
An htmx event listener reads the headers to tell the two outcomes apart.

.. code-block:: html
   :caption: reacting to a validation failure

   <script>
     document.body.addEventListener("htmx:afterRequest", (event) => {
       const xhr = event.detail.xhr;
       if (xhr.getResponseHeader("X-Next-Form") === "invalid") {
         const uid = xhr.getResponseHeader("X-Next-Action");
         document.querySelector(`form[data-next-action="${uid}"]`).classList.add("has-errors");
       }
     });
   </script>

Branch on ``request.htmx`` With django-htmx
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The django-htmx middleware coexists with the dispatcher and annotates every request, so ``on_valid`` and action handlers branch on ``request.htmx``.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       # ...
       "django_htmx.middleware.HtmxMiddleware",
   ]

.. code-block:: python
   :caption: contact/pages/contact/page.py

   from django import forms
   from django.http import HttpRequest, HttpResponseRedirect

   import next.forms

   class ContactForm(next.forms.Form):
       name = forms.CharField(max_length=100)
       email = forms.EmailField()

       def on_valid(self, request: HttpRequest) -> HttpResponseRedirect:
           if request.htmx:
               return HttpResponseRedirect("/contact/thanks-fragment/")
           return HttpResponseRedirect("/contact/thanks/")

Verification
------------

Submit the form with an invalid value in the browser.
Only the ``#contact`` region swaps, the entered values stay, and the errors show.
A ``NextClient`` test asserts the server side of the same flow.

.. code-block:: python
   :caption: tests/test_contact_htmx.py

   from next.testing.client import NextClient

   def test_invalid_submit_signals_rerender() -> None:
       client = NextClient()
       resp = client.post_action(
           "contact_form",
           {"name": "Ada", "email": "nope"},
           origin="/contact/",
           headers={"hx-request": "true"},
       )
       assert resp.status_code == 200
       assert resp["X-Next-Form"] == "invalid"
       assert 'value="Ada"' in resp.content.decode()

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/templates` for attribute passthrough and the reserved names.
   :doc:`/content/topics/forms/validation-rerender` for the re-render contract and the headers.
   :doc:`test-a-page-with-actions` for the testing pattern.
