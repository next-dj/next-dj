.. _topics-forms-wizard:

Form Wizards
============

A multi-step form across several requests usually means hand-rolling step routing, stashing partial data in the session, and re-wiring all of it to support the browser back button or a branch that skips a step.
A ``next.forms.FormWizard`` carries that load.
It routes a sequence of ordinary forms across requests and finalises once on the last step.
Each step is a plain ``next.forms.Form`` or ``next.forms.ModelForm``.
The wizard handles step routing and back-navigation, supports conditional branching through ``steps_for``, persists per-step drafts through the configured wizard backend, and calls ``done`` with the merged cleaned data after the final step.

.. contents::
   :local:
   :depth: 2

Mental Model
------------

One wizard is one registered action.
Subclassing ``next.forms.FormWizard`` registers the class through the ``__init_subclass__`` hook the moment Python runs the ``class`` statement, exactly like a plain form.
The action name is the ``snake_case`` of the class name, so ``AccessRequestWizard`` becomes ``access_request_wizard``.

The scope rules match plain forms.
A wizard declared in ``page.py`` is page-scoped and keyed to its file.
A wizard declared in any other module is shared and reachable project-wide.
See :doc:`overview` for the full scope derivation.

Declaring Steps
---------------

Declare the ordered steps under ``Meta.steps`` as a list of ``(name, FormClass)`` tuples.
Each form class is a normal ``next.forms.Form`` or ``next.forms.ModelForm`` and follows every rule on the rest of these pages.

.. code-block:: python
   :caption: access/views/request/[step]/page.py — auto-registered as ``access_request_wizard``

   import next.forms
   from access.models import AccessRequest
   from next.forms import redirect_to_origin

   class IdentityStep(next.forms.ModelForm):
       class Meta:
           model = AccessRequest
           fields = ["full_name", "email", "team"]
           abstract = True

   class ScopeStep(next.forms.ModelForm):
       class Meta:
           model = AccessRequest
           fields = ["project_slug", "reason", "expires_in_days"]
           abstract = True

   class ApprovalStep(next.forms.Form):
       """Final step that only confirms the merged request."""

       class Meta:
           abstract = True

   class AccessRequestWizard(next.forms.FormWizard):
       class Meta:
           steps = [
               ("identity", IdentityStep),
               ("scope", ScopeStep),
               ("approval", ApprovalStep),
           ]
           url_param = "step"

       def done(self, request, cleaned_data):
           AccessRequest.objects.create(**cleaned_data)
           return redirect_to_origin(request)

Every step form sets ``Meta.abstract = True``.
A step is not a standalone action: without the flag, ``__init_subclass__`` registers each step as its own form action whose default ``on_valid`` saves a partial row.
The flag suppresses that registration while the wizard still drives the class through ``Meta.steps``, see :ref:`Preventing Registration <topics-forms-actions-abstract>`.

``Meta.steps`` is required.
An empty or missing list triggers the ``next.E050`` system check and the wizard is not usable.

``Meta.url_param`` names the URL kwarg that carries the active step.
It defaults to ``"step"``, so a route segment of ``[step]`` works with no further configuration.

Per-step drafts persist through the configured ``DEFAULT_FORM_WIZARD_BACKEND``, which the project sets once for every wizard.
See :doc:`wizard-backend` for the backend contract and its options.

The done Method
---------------

``done`` runs once after the last step validates.
It receives ``request`` and the merged ``cleaned_data`` of every stored step, so the keys from each step form are flattened into one mapping.
For a wizard whose steps are ModelForms over a single model, the merged dict maps straight onto a model constructor.

.. code-block:: python
   :caption: finalising the wizard

   def done(self, request, cleaned_data):
       AccessRequest.objects.create(**cleaned_data)
       return redirect_to_origin(request)

``done`` is required.
The base implementation raises ``NotImplementedError``, so every wizard subclass must override it.
Return any ``HttpResponse``, most often a redirect away from the wizard.

Keep ``done`` idempotent.
A retried final submission can run it again, so guard against creating a duplicate row.
Make ``done`` safe to run twice with the same data, for example by using ``get_or_create`` instead of ``create``.

The backend performs an unlocked read-modify-write, so concurrent submissions from two browser tabs overwrite each other's step data.
Last write wins is the deliberate design for a single linear flow.
An idempotent ``done`` keeps the final write correct under retries, which is the case that matters.
A flow that must serialise concurrent tabs needs a custom backend that locks or compares and swaps the stored bucket.

``done`` Takes a Fixed Signature
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``done`` is not dependency-injected.
The dispatcher calls ``wizard.done(request, cleaned_data)`` directly, so the method always takes exactly ``self``, ``request``, and ``cleaned_data``.
This differs from an action handler or ``on_valid``, where the injector resolves extra parameters.
A ``done`` method cannot declare ``DUrl[...]`` markers or named providers and expect them to fill.
Read the URL kwargs from ``self.url_kwargs`` and any provider value from inside ``done`` when one is needed.

The ``done`` Return Value
~~~~~~~~~~~~~~~~~~~~~~~~~~

The return value of ``done`` follows the same coercion as an action handler.

- An ``HttpResponse`` subclass is sent as is. A redirect is the usual choice.
- A string becomes an ``HttpResponse`` body with status 200.
- A value with a truthy ``url`` attribute becomes an ``HttpResponseRedirect`` to that URL.
- ``None`` coerces to a success response that re-renders the origin page.

.. warning::

   Returning ``None`` from ``done`` still finalises the wizard.
   The dispatcher coerces ``None`` into a success re-render of the origin page with status 200, so the stored drafts are cleared and ``wizard_completed`` fires.
   The user is left on the last step with no confirmation, so return an explicit redirect away from the wizard.

How Step Forms Differ From Standalone Forms
-------------------------------------------

A wizard step is a plain ``next.forms.Form`` or ``next.forms.ModelForm``, but the wizard drives it through a different path than a standalone form action.
Two hooks that fire for a standalone form never fire for a step.

``get_initial`` is not called on POST.
   The dispatcher builds the step form as ``form_class(request.POST, files, **get_form_kwargs())``.
   It never calls ``get_initial`` on the step class during validation.
   Prefilling a step instead flows through the saved draft, which the wizard injects as ``initial`` when it builds the unbound form for a GET.
   Pass cross-step values through ``get_form_kwargs`` on the wizard, not through ``get_initial`` on the step.

``on_valid`` is never called.
   A standalone form runs ``on_valid`` after it validates.
   A step does not.
   The wizard saves the cleaned data, advances to the next step, and runs ``done`` once after the final step.
   Put per-step side effects nowhere, and put the single finalising write in ``done``.

A step that defines ``get_initial`` or ``on_valid`` sees neither method run inside the wizard.
Both are silently inert on a step class.

Rendering
---------

Render the wizard with the same ``{% form %}`` block tag a plain form uses.

.. code-block:: jinja
   :caption: access/views/request/[step]/template.djx

   {% form "access_request_wizard" %}
     {{ form.as_p }}
     <button type="submit">
       {% if wizard.is_last %}Submit{% else %}Continue{% endif %}
     </button>
   {% endform %}

Inside the block the tag publishes two variables.
``form`` is the current step's form, unbound on a GET and prefilled from the saved draft when the step was visited before.
``wizard`` is the ``FormWizard`` instance, which exposes the navigation helpers below.

Wizard Template API
~~~~~~~~~~~~~~~~~~~~

The ``wizard`` variable carries the methods used to build progress indicators and navigation.
The zero-argument methods are auto-called by Django templates, so a template writes ``{{ wizard.current_step }}`` without parentheses.

.. list-table::
   :header-rows: 1
   :widths: 25 45 30

   * - Method
     - Returns
     - Notes
   * - ``current_step()``
     - The active step name, read from the URL kwarg and defaulting to the first step.
     - Zero-argument, usable as ``{{ wizard.current_step }}``.
   * - ``step_names()``
     - The ordered step names for this request, after any ``steps_for`` filtering.
     - Zero-argument, iterable as ``{% for name in wizard.step_names %}``.
   * - ``is_first()``
     - ``True`` when the current step is the first step.
     - Zero-argument.
   * - ``is_last()``
     - ``True`` when the current step is the last step.
     - Zero-argument.
   * - ``completed_steps()``
     - The names of steps that already have a saved draft.
     - Zero-argument.
   * - ``cleaned_data_so_far()``
     - The merged cleaned data of every saved step.
     - Zero-argument.
   * - ``goto(step)``
     - The page URL for ``step``, derived from the current page path by swapping the step segment.
     - Takes an argument, so call it from a ``@page.context`` or ``@component.context`` function and publish the result.

A progress bar reads the step status in Python and iterates the precomputed list in the template.

.. code-block:: python
   :caption: access/views/request/[step]/_blocks/progress_bar/component.py

   from typing import Any

   from next.components import component
   from next.forms import FormWizard

   @component.context("steps")
   def steps(wizard: FormWizard) -> list[dict[str, Any]]:
       current = wizard.current_step()
       completed = set(wizard.completed_steps())
       return [
           {
               "key": name,
               "url": wizard.goto(name),
               "status": _status(name, current, completed),
           }
           for name in wizard.step_names()
       ]

   def _status(key: str, current: str, completed: set[str]) -> str:
       if key == current:
           return "current"
       if key in completed:
           return "saved"
       return "pending"

.. code-block:: jinja
   :caption: access/views/request/[step]/_blocks/progress_bar/component.djx

   <nav>
     {% for step in steps %}
       <a href="{{ step.url }}" data-status="{{ step.status }}">
         {{ step.key }}
       </a>
     {% endfor %}
   </nav>

Routing and Back-Navigation
---------------------------

The wizard lives on a route that captures the step segment, such as ``request/[step]/``.
The captured kwarg name matches ``Meta.url_param``.

On a valid non-final step the dispatcher saves the step data and redirects to the next step's URL.
The redirect reuses the current page path and swaps the step segment, so ``request/identity/`` becomes ``request/scope/``.
On a valid final step the dispatcher merges every stored step and calls ``done``.
An invalid step re-renders the current step with its errors, and the draft already saved for earlier steps is left untouched.

Back-navigation works through the same URL.
Visiting an earlier step prefills its form from the saved draft, so the user sees the values they entered before.
The current step is always resolved from the URL kwarg, which keeps the browser back button and bookmarked step URLs working.

The ``form`` Variable Has Two Sources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``form`` published inside the ``{% form %}`` block is built differently on a GET than on a validation failure.

- On a GET the wizard returns ``current_form()``, an unbound form prefilled with the saved draft as its ``initial`` data.
- On a validation failure the dispatcher injects the bound failing form directly, so ``form`` carries the rejected values and the field errors.

The template variable is the same name in both cases.
A template that renders ``{{ form.field.errors }}`` shows nothing on a clean GET and the validation errors on a re-render.

Conditional Steps
-----------------

Override ``steps_for`` to choose the step list from the data gathered so far.
The hook reads the accumulated data through ``self.cleaned_data_so_far()`` and returns the same ``[(name, FormClass), ...]`` shape as ``Meta.steps``.

.. code-block:: python
   :caption: dropping the approval step for low-risk requests

   def steps_for(self):
       steps = [("identity", IdentityStep), ("scope", ScopeStep)]
       if self.cleaned_data_so_far().get("expires_in_days", 0) > 7:
           steps.append(("approval", ApprovalStep))
       return steps

When ``steps_for`` is not overridden it returns ``Meta.steps`` unchanged.
The navigation helpers, the current-step resolution, and the final-step detection all read from ``steps_for``, so a conditional list flows through routing without extra wiring.

Cross-Step Inputs
-----------------

Override ``get_form_kwargs`` to pass extra constructor arguments into a step form.
The hook reads the active step through ``self.current_step()`` and the merged cleaned data through ``self.cleaned_data_so_far()``, and returns a dict of keyword arguments for the step form constructor.

.. code-block:: python
   :caption: seeding the approval step from an earlier choice

   def get_form_kwargs(self):
       if self.current_step() == "approval":
           return {"reviewer_pool": teams_for(self.cleaned_data_so_far().get("team"))}
       return {}

A step form that accepts ``reviewer_pool`` reads it in its own ``__init__`` to build a field choice list.
The default ``get_form_kwargs`` returns an empty dict, so steps that need nothing extra require no override.

Signals
-------

The wizard emits ``wizard_step_submitted`` after each step validates and ``wizard_completed`` after ``done`` runs for the final step.
Both are sent by ``FormActionDispatch``.
See :doc:`signals` for the payloads and the receiver-wiring pattern.

System Checks
-------------

The ``next.E050`` and ``next.E051`` checks guard the steps declaration and the wizard backend configuration.
See :doc:`/content/ref/system-checks` for their conditions, and run ``uv run python manage.py check`` after editing a wizard or its backend.

See Also
--------

.. seealso::

   :doc:`wizard-backend` for the backend contract and the cache-backed default.
   :doc:`overview` for auto-registration and scope.
   :doc:`modelforms` for the ModelForm steps the example uses.
   :doc:`signals` for the wizard signals.
   :doc:`/content/howto/build-multi-step-wizard` for a step-by-step recipe.
   :doc:`/content/topics/file-router` for the ``[step]`` route segment.
