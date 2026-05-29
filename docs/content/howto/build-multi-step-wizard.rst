.. _howto-multi-step-wizard:

Build a Multi-Step Wizard
=========================

Problem
-------

You want a multi-step form that gathers data across several screens and writes one row at the end, without hand-rolling step routing or session juggling.

Solution
--------

Declare a ``next.forms.FormWizard`` with one form per step under ``Meta.steps``.
Put the wizard on a route that captures the step segment.
Implement ``done`` to create the row from the merged cleaned data.

Walkthrough
-----------

Declare the Steps and Wizard
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each data step is an ordinary ``ModelForm`` over the target model, and the final step only confirms the merged request.
The wizard lists them in order.

.. code-block:: python
   :caption: access/views/request/[step]/page.py

   import next.forms
   from access.models import AccessRequest
   from next.forms import redirect_to_origin

   class IdentityStep(next.forms.ModelForm):
       class Meta:
           model = AccessRequest
           fields = ["full_name", "email", "team"]

   class ScopeStep(next.forms.ModelForm):
       class Meta:
           model = AccessRequest
           fields = ["project_slug", "reason", "expires_in_days"]

   class ApprovalStep(next.forms.Form):
       """Final step that only confirms the merged request."""

   class AccessRequestWizard(next.forms.FormWizard):
       class Meta:
           steps = [
               ("identity", IdentityStep),
               ("scope", ScopeStep),
               ("approval", ApprovalStep),
           ]

       def done(self, request, cleaned_data):
           AccessRequest.objects.create(**cleaned_data)
           return redirect_to_origin(request)

The wizard registers itself as the ``access_request_wizard`` action through subclassing.
``Meta.url_param`` defaults to ``"step"``, which matches the ``[step]`` route segment, so no extra configuration is needed.

Route Through the Step Segment
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The page directory is ``request/[step]/``, so the route captures a ``step`` kwarg.
The wizard reads that kwarg to pick the current step and swaps the segment when it advances.

Render the Wizard
~~~~~~~~~~~~~~~~~~

The ``{% form %}`` tag publishes ``form`` for the current step and ``wizard`` for navigation.

.. code-block:: jinja
   :caption: access/views/request/[step]/template.djx

   {% form "access_request_wizard" %}
     {{ form.as_p }}
     <button type="submit">
       {% if wizard.is_last %}Submit request{% else %}Continue{% endif %}
     </button>
   {% endform %}

A valid non-final step saves its data and redirects to the next step's URL.
A valid final step calls ``done``, which creates the row and redirects away.
An invalid step re-renders with errors and leaves the earlier steps' saved drafts untouched.

Finalise the Wizard
~~~~~~~~~~~~~~~~~~~~

``done`` receives the merged cleaned data of every step.
Because the data steps are ModelForms over the same model, the merged dict maps straight onto the model constructor.

.. code-block:: python
   :caption: the done method

   def done(self, request, cleaned_data):
       AccessRequest.objects.create(**cleaned_data)
       return redirect_to_origin(request)

Verification
------------

Walk the flow once.
Fill the first step, advance through the rest, and confirm the final submission creates one row.
Use the browser back button on an earlier step and confirm the values you entered reappear.

A test asserts the same flow with ``NextClient``.
The ``{% form %}`` tag emits three hidden fields the dispatcher reads back on POST.
``_url_param_step`` carries the current step, ``_next_form_origin`` is the page path the next-step redirect is derived from, and ``_next_form_page`` is the page module that re-renders on a validation error.
A test posts them alongside the step's own fields.

.. code-block:: python
   :caption: tests/test_wizard.py

   from pathlib import Path

   from access.models import AccessRequest
   from next.testing.client import NextClient

   STEP_PAGE = str(
       Path(__file__).resolve().parent.parent / "access" / "views" / "request" / "[step]" / "page.py"
   )

   def post_step(client, step, data):
       payload = dict(data)
       payload["_url_param_step"] = step
       payload["_next_form_origin"] = f"/request/{step}/"
       payload["_next_form_page"] = STEP_PAGE
       return client.post_action("access_request_wizard", payload)

   def test_wizard_flow(db) -> None:
       client = NextClient()
       post_step(client, "identity", {"full_name": "Ada", "email": "ada@example.com", "team": "core"})
       post_step(client, "scope", {"project_slug": "atlas", "reason": "audit", "expires_in_days": 7})
       post_step(client, "approval", {})
       assert AccessRequest.objects.filter(email="ada@example.com").exists()

Each ``post_step`` targets one step through ``_url_param_step``.
The final step's submission triggers ``done`` and the row appears.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/wizard` for the wizard topic guide.
   :doc:`/content/topics/forms/wizard-backend` for the wizard backend.
   :doc:`/content/topics/forms/modelforms` for the ModelForm steps.
   :doc:`use-modelform-for-crud` for the single-form CRUD pattern.
