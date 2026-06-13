.. _topics-forms:

Forms
=====

next.dj wraps Django forms with a registry, a stable POST endpoint, and a re-render pipeline that survives validation failures.
This section walks every layer of the system from the page module down to the dispatch backend.
Read :doc:`overview` first, then jump to the page that matches the part you are extending.

.. rubric:: Concepts

:doc:`overview`
   The mental model behind actions, dispatch, and re-render.

.. rubric:: Building forms

:doc:`actions`
   Register a handler with ``@action`` and decide which parameters to ask for.
   Also covers the access guards (``login_required``, ``permission_required``) and the success contracts (``success_url``, ``success_message``).

:doc:`templates`
   Render a form with ``{% form %}`` and link several forms on a single page.

:doc:`modelforms`
   Use Django ``ModelForm`` for create and edit pages.

:doc:`plain-forms`
   Use a plain ``Form`` for filter, search, voting, and bulk-operation pages.

:doc:`field-components`
   Render a field through a next.dj component with ``ComponentWidget``.

:doc:`formsets`
   Render and validate Django formsets through the same dispatch pipeline.

.. rubric:: Multi-step

:doc:`wizard`
   Route a sequence of forms across requests and finalise on the last step.

:doc:`wizard-backend`
   Persist per-step draft data through the configured wizard backend.

.. rubric:: Mechanics

:doc:`validation-rerender`
   What happens between a failing POST and the re-rendered origin page.

.. rubric:: Extending

:doc:`serializers`
   The frozen field, formset, and form specs for rendering a form in a custom template engine.

:doc:`backends`
   Swap the validation pipeline through ``FORM_ACTION_BACKENDS``.

:doc:`signals`
   Every signal the forms subsystem emits, with payload details.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   actions
   templates
   modelforms
   plain-forms
   field-components
   formsets
   wizard
   wizard-backend
   validation-rerender
   serializers
   backends
   signals
