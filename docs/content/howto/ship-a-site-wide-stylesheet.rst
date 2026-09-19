.. _howto-site-wide-stylesheet:

Ship a site-wide stylesheet
===========================

Problem
-------

Every page of the site must carry the same stylesheet, and repeating a ``<link>`` tag in each template is not an option.

Solution
--------

Put the styles the project owns in a ``layout.css`` beside the root ``layout.djx``, and name anything staticfiles serves from a ``{% use_style %}`` tag in that same root layout.
A layout wraps every page below it, so one declaration covers the whole tree.

Walkthrough
-----------

Styles the project owns
~~~~~~~~~~~~~~~~~~~~~~~

A ``layout.css`` next to the root ``layout.djx`` is discovered as that layout's asset and needs no tag at all.

.. code-block:: text
   :caption: notes/pages/

   layout.djx
   layout.css
   page.py
   template.djx

Every page under ``notes/pages/`` collects ``layout.css``, because the root layout takes part in every layout chain.
An inner ``layout.css`` scopes its styles to the pages below that directory, which is the same mechanism one level down.
Layout assets enter the collector before page and component assets, so the cascade runs from the shell inwards.

A file staticfiles serves
~~~~~~~~~~~~~~~~~~~~~~~~~

A stylesheet that no page or component owns, such as a design token file shared by several applications, lives in an application ``static/`` directory and is named from the root layout.

.. code-block:: text
   :caption: notes/static/site/

   tokens.css

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html>
     <head>
       {% use_style "site/tokens.css" %}
       {% collect_styles %}
     </head>
     <body>
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

``site/tokens.css`` is a staticfiles name, so it resolves through ``STATIC_URL`` and carries the manifest hash in production, see :doc:`/content/topics/static-assets/name-resolution`.
Tag assets are prepended to the collector, so the named file loads before the co-located ``layout.css`` and before any ``component.css``.

A vendor file on a CDN
~~~~~~~~~~~~~~~~~~~~~~

A stylesheet hosted elsewhere is written as the URL it already has, in the same tag and the same layout.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   {% use_style "https://cdn.example.com/reset.css" %}
   {% use_style "site/tokens.css" %}

The URL carries a scheme, so it reaches the document unchanged.
Both tags prepend, and the collector preserves the order they were written in.

Choosing between the three
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - The file
     - The spelling
   * - Written for the root layout and living beside it.
     - ``layout.css``, discovered with no tag.
   * - Served by staticfiles and owned by no template.
     - ``{% use_style "<name>" %}`` in the root layout.
   * - Hosted on a third-party origin.
     - ``{% use_style "<url>" %}`` in the root layout.

A compiled bundle that a build tool produces is the second row, and :doc:`use-a-compiled-stylesheet` covers where the output goes and how it reaches production.

Verification
------------

Load any page and read the rendered ``<head>``.
The slot emitted by ``{% collect_styles %}`` holds one ``<link>`` per stylesheet, the named file ahead of the co-located one.

Confirm that staticfiles can see the named file.

.. code-block:: bash
   :caption: shell

   uv run python manage.py findstatic site/tokens.css

A page that lists the same stylesheet twice still emits one tag, because the collector deduplicates on the resolved URL.

See also
--------

.. seealso::

   :doc:`/content/topics/layouts` for the layout chain the declaration rides on.
   :doc:`/content/topics/static-assets/name-resolution` for the rule that decides a name from a URL.
   :doc:`/content/topics/static-assets/co-located-files` for the stem convention behind ``layout.css``.
   :doc:`use-a-compiled-stylesheet` for a stylesheet produced by a bundler.
