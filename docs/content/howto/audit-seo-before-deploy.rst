.. _howto-audit-seo:

Audit SEO before deploy
=======================

Problem
-------

A metadata key of the wrong shape, a sitemap route the tree no longer serves, a deployment without its public origin, or a page that silently turned ``noindex`` should fail the pipeline rather than reach a crawler.

Solution
--------

Run ``manage.py check --deploy --tag seo`` against the production settings in CI, pin the head of the pages that matter with ``next.testing.assert_metadata``, read the sitemap back with ``parse_sitemap``, and reach for ``manage.py showmetadata`` when a key comes out other than expected.

Walkthrough
-----------

Run the checks against the deploy settings
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``seo`` tag narrows the run to the metadata, sitemap, robots, and site checks, and ``--deploy`` adds the site checks a deployment needs.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check --deploy --tag seo --settings=config.settings.prod

Run it against the settings the deployment uses, since the site checks read its ``SITE`` scope and ``DEBUG``.
Every message names the file it concerns and the fix it expects.

Pin the head in a test
~~~~~~~~~~~~~~~~~~~~~~

The checks read the static metadata alone, so what a ``@page.metadata`` callable builds per row belongs to a test.

.. code-block:: python
   :caption: notes/tests/test_seo.py

   from django.test import override_settings

   from next.testing import NextClient, assert_metadata, parse_sitemap

   @override_settings(DEBUG=False)
   def test_note_head(note):
       response = NextClient().get(f"/notes/{note.pk}/")
       assert_metadata(
           response,
           title=f"{note.title} · Notes",
           description=note.summary,
           canonical=f"https://notes.example/notes/{note.pk}/",
           robots=None,
       )

   @override_settings(DEBUG=False)
   def test_draft_stays_out(draft):
       response = NextClient().get(f"/notes/{draft.pk}/")
       assert_metadata(response, robots="noindex, follow")
       urls = parse_sitemap(NextClient().get("/sitemap.xml"))
       assert f"https://notes.example/notes/{draft.pk}/" not in {url.loc for url in urls}

The second test covers a draft whose callable answers ``"robots": {"index": False}``, which the items callable of the sitemap has to leave out as well.
``DEBUG=False`` opens the site to search, so the robots meta shows what the pages declare rather than the ``noindex, nofollow`` of development.

Gate the pipeline
~~~~~~~~~~~~~~~~~

.. code-block:: yaml
   :caption: .github/workflows/ci.yml

   - name: SEO
     run: |
       uv run python manage.py check --deploy --tag seo --fail-level WARNING
       uv run pytest notes/tests/test_seo.py
     env:
       DJANGO_SETTINGS_MODULE: config.settings.ci

The ``--tag seo`` narrowing matters, because without it ``--fail-level WARNING`` also fails on Django's own ``security.W0xx`` deployment warnings, which a CI settings module usually triggers on purpose.
Run Django's deployment checks in a separate step against the production settings.

Explain a surprising key
~~~~~~~~~~~~~~~~~~~~~~~~

``showmetadata`` prints which layer sets each key of a page, ``DEFAULTS`` or a ``page.py``, and lists the callables after them.

.. code-block:: bash
   :caption: shell

   uv run python manage.py showmetadata /notes/42/

Verification
------------

Unset ``SITE["URL"]`` in the CI settings and run the checks.
The run warns that canonical, Open Graph, sitemap, and robots URLs follow the ``Host`` header, and fails the step.
Set ``SITE["INDEXABLE"]`` to ``False`` on a tree that serves a ``sitemap.py``, and the run warns about a deployment that would drop out of every search index while still inviting crawlers.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/auditing` for the checks, ``showmetadata``, and the test helpers.
   :doc:`/content/ref/system-checks` for every code and its condition.
   :doc:`/content/deployment/checklist` for the rest of the deploy script.
