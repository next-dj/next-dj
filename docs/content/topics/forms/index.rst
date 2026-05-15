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

:doc:`templates`
   Render a form with ``{% form %}`` and link several forms on a single page.

:doc:`modelforms`
   Use Django ``ModelForm`` for create and edit pages.

:doc:`formsets`
   Render and validate Django formsets through the same dispatch pipeline.

.. rubric:: Mechanics

:doc:`validation-rerender`
   What happens between a failing POST and the re-rendered origin page.

:doc:`serializers`
   The frozen field, formset, and form specs that survive across re-render.

.. rubric:: Extending

:doc:`backends`
   Swap the validation pipeline through ``DEFAULT_FORM_ACTION_BACKENDS``.

:doc:`signals`
   Every signal the forms subsystem emits, with payload details.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   actions
   templates
   modelforms
   formsets
   validation-rerender
   serializers
   backends
   signals
