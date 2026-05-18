.. _intro-whatsnext:

What to Read Next
=================

The tutorial covers the core flow.
After :doc:`tutorial05`, follow **topics → how-to guides → reference → internals** when you need depth beyond the Notes walkthrough.

The hubs below replace long subsystem-by-subsystem lists.

- :doc:`/content/topics/index` explains each subsystem.
- :doc:`/content/howto/index` answers task-shaped questions.
- :doc:`/content/ref/index` lists modules, decorators, settings, and checks.
- :doc:`/content/internals/index` traces pipelines under the hood.
- :doc:`/content/deployment/index` and :doc:`/content/security/index` cover operations.
- :doc:`/content/misc/examples` catalogues the repository ``examples/`` projects with links and doc cross-references.

Learning Paths
--------------

First Full-Stack App
~~~~~~~~~~~~~~~~~~~~

1. :doc:`install`
2. :doc:`tutorial01` through :doc:`tutorial05`
3. :doc:`/content/topics/file-router` — route shapes and captured segments.
4. :doc:`/content/topics/context` and :doc:`/content/topics/dependency-injection` — shared scope and markers such as ``DUrl``.
5. :doc:`/content/topics/forms/index`
6. :doc:`/content/topics/static-assets/index`
7. :doc:`/content/deployment/checklist`

Customize the Pipeline
~~~~~~~~~~~~~~~~~~~~~~

1. :doc:`/content/topics/extending`
2. :doc:`/content/topics/static-assets/backends`
3. :doc:`/content/topics/forms/backends`
4. :doc:`/content/topics/signals`
5. :doc:`/content/internals/overview`
6. :doc:`/content/internals/action-dispatch` and :doc:`/content/internals/component-pipeline`
7. :doc:`/content/howto/add-a-custom-template-loader`
8. :doc:`/content/howto/observe-framework-signals`

Multi-Tenant or Multi-Project Setup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. :doc:`/content/topics/multi-project`
2. :doc:`/content/howto/share-components-across-projects`
3. :doc:`/content/howto/scope-requests-per-tenant`
4. :doc:`/content/topics/dependency-injection`
5. :doc:`/content/deployment/settings`

Examples
--------

The ``examples/`` directory contains several self-contained Django projects plus ``_shared`` UI kit assets and a ``_template`` scaffold.
Run any example from its folder with ``migrate`` then ``runserver``.
Conventions, commands, and a per-folder **Focus** summary are in the repository `examples README <https://github.com/next-dj/next-dj/blob/main/examples/README.md>`_.
The documentation catalog with GitHub links and primary doc pointers lives in :doc:`/content/misc/examples`.

Community
---------

Source code lives at ``github.com/next-dj/next-dj``.
File an issue or open a discussion when something is unclear.
Contributions to the documentation are welcome, see :doc:`/content/contributing/index`.
