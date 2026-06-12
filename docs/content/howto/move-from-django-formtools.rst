.. _howto-from-formtools:

Move From django-formtools
==========================

Problem
-------

You maintain a multi-step flow built on the django-formtools ``WizardView`` and want the equivalent in next.dj.

Solution
--------

Port the flow to a ``next.forms.FormWizard``.
The step forms move unchanged, the wizard hooks keep the formtools vocabulary, and the URL wiring disappears.
Declare the ordered steps under ``Meta.steps``, place the wizard on a route with a ``[step]`` segment, and move the finalising code into ``done``.
:doc:`build-a-multi-step-wizard` walks through a complete wizard from scratch.

Method Map
----------

The wizard API deliberately reuses the formtools names where the semantics match.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - formtools ``WizardView``
     - ``next.forms.FormWizard``
   * - ``form_list`` / ``as_view(form_list=...)``
     - ``Meta.steps``, an ordered list of ``(name, FormClass)`` tuples.
   * - ``condition_dict``
     - A ``get_steps()`` override returning the filtered step list.
   * - ``get_form_kwargs(step)``
     - ``get_form_kwargs(step)``, same name and shape.
   * - ``get_all_cleaned_data()``
     - ``get_all_cleaned_data()``, same name, returns the merged dict of every stored step.
   * - ``get_cleaned_data_for_step(step)``
     - ``get_cleaned_data_for_step(step)``, same name, returns ``None`` when the step has no stored data.
   * - ``done(form_list, **kwargs)``
     - ``done(request, cleaned_data)``, receives the merged cleaned data instead of form instances.
   * - ``NamedUrlSessionWizardView`` and the named step URLs
     - The ``[step]`` route segment, matched by the default ``Meta.url_param`` of ``"step"``.
   * - ``storage_name`` with the session and cookie storages
     - The ``FORM_WIZARD_BACKEND`` setting with the session and cache backends.
   * - ``file_storage``
     - No equivalent, see `Files in Steps`_.
   * - ``as_view()`` mounted in ``urlpatterns``
     - Auto-registration on subclassing, no URLconf entry.

What Changes in Substance
-------------------------

No URL Plumbing
~~~~~~~~~~~~~~~

A ``WizardView`` is a class-based view mounted with ``as_view`` in ``urlpatterns``.
A ``FormWizard`` registers itself the moment Python runs the ``class`` statement and lives on a routed page, where the ``[step]`` directory captures the current step.
See :doc:`/content/topics/forms/wizard` for the registration and scope rules.

Steps Live in the URL
~~~~~~~~~~~~~~~~~~~~~

The base ``WizardView`` keeps the current step in storage behind a single URL, and only ``NamedUrlWizardView`` exposes it.
The ``FormWizard`` always resolves the current step from the URL segment, so bookmarks and the browser back button work by construction.
There is no ``{{ wizard.management_form }}`` and no ``wizard_goto_step`` POST field.
Navigation to an earlier step is a plain link to its URL, built with ``wizard.goto(step)``.

``done`` Receives Data, Not Forms
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

formtools revalidates every form at the end and passes the instances into ``done``.
The ``FormWizard`` persists each step's cleaned data through the backend as it validates, then passes one merged dict.
Per-step access goes through ``get_cleaned_data_for_step``.
A field declared by two steps merges to the last stored value, and the ``next.W059`` system check warns about such collisions.

Validation Re-Render Is Free
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An invalid step re-renders the page with the bound failing form under the same ``form`` variable the template already uses.
There is no per-step template selection and no ``{{ wizard.form }}`` indirection, the one ``{% form %}`` block covers every step.

Files in Steps
~~~~~~~~~~~~~~

formtools supports uploads inside steps through ``file_storage``.
The ``FormWizard`` has no equivalent yet.
Wizard storage persists cleaned data between requests, and an uploaded file does not survive that round trip, so the ``next.W058`` system check warns when a static step declares a ``FileField``.
Collect the upload in a standalone form action instead, see :doc:`handle-file-uploads`.

Verification
------------

Run the system checks after the port.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

``next.E050`` catches an empty step list, ``next.W057`` a step class doubling as a standalone action, ``next.W058`` a file field in a step, and ``next.W059`` a field declared by two steps.
Then walk the flow once: fill the first step, use the browser back button to confirm the draft reappears, and finish to confirm ``done`` runs exactly once.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/wizard` for the full wizard topic guide.
   :doc:`/content/topics/forms/wizard-backend` for the storage backends.
   :doc:`build-a-multi-step-wizard` for the step-by-step recipe.
   :doc:`/content/ref/system-checks` for the check conditions.
