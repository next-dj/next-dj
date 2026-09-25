.. _howto-audit-seo:

Audit SEO before deploy
=======================

Problem
-------

A page shipped without a description, two pages sharing a title, or a canonical pointing at a URL the project does not serve should fail the pipeline rather than reach a crawler.

Solution
--------

Run the opt-in content audits with ``manage.py check --deploy --tag seo`` in CI and in the deploy script, tune the thresholds under ``NEXT_FRAMEWORK["METADATA"]["CHECKS"]``, and silence the one or two warnings that describe a deliberate shape by their id.

Walkthrough
-----------

Run the audit locally
~~~~~~~~~~~~~~~~~~~~~

The ``seo`` tag narrows the run to the SEO checks, and ``--deploy`` adds the audits to them.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy --tag seo

The command runs the four audits, ``next.W089`` to ``next.W096``, beside the sitemap and robots checks that carry the tag too, so its output is the SEO report alone.
Every message names the file it concerns and the fix it expects.

Set the thresholds
~~~~~~~~~~~~~~~~~~

The audits compare titles and descriptions against the numbers the site works to.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "CHECKS": {
               "TITLE_MAX": 70,
               "DESCRIPTION_MAX": 155,
               "REQUIRE_DESCRIPTION": True,
           },
       },
   }

Silence a deliberate warning
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A warning that names an intended shape is dropped by its id.

.. code-block:: python
   :caption: config/settings.py

   SILENCED_SYSTEM_CHECKS = ["next.W090"]

The setting drops the message and still counts it in the closing line of the run, so the fact that the project overrides a check stays visible.

Gate the pipeline
~~~~~~~~~~~~~~~~~

The deploy fails on any warning the audit raises.

.. code-block:: bash
   :caption: .github/workflows/ci.yml

   uv run python manage.py check --deploy --fail-level WARNING

The ``--fail-level WARNING`` flag makes the command exit non-zero on a warning, and dropping ``--tag seo`` runs Django's own deployment checks and the other framework deployment checks in the same pass.

Verification
------------

Add a page with no description and run the tagged audit.

.. code-block:: python
   :caption: notes/pages/scratch/page.py

   metadata = {"title": "Scratch"}

The run reports ``next.W089`` for ``notes/pages/scratch/page.py``.
Declare a description of at least 50 characters, or a site-wide one in ``DEFAULTS``, and the run reports nothing.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/auditing` for the two tiers of checks and what they can see.
   :doc:`/content/ref/system-checks` for every code and its condition.
   :doc:`/content/deployment/checklist` for the rest of the deploy script.
