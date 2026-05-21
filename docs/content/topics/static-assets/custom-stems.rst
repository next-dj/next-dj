.. _topics-static-custom-stems:

Custom Stems
============

A stem is the filename without the extension.
The framework ships ``template``, ``layout``, and ``component`` as default stems.
This page covers how to register new stems so discovery picks up additional filenames.

.. contents::
   :local:
   :depth: 2

The Stem Registry
-----------------

The stem registry is ``next.static.discovery.default_stems``, an instance of ``StemRegistry``.
It maps a role to a list of stems.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Role
     - Default stem
     - Matches
   * - ``template``
     - ``template``
     - Files next to ``template.djx``.
   * - ``layout``
     - ``layout``
     - Files next to ``layout.djx``.
   * - ``component``
     - ``component``
     - Files inside a component folder.

A role can hold several stems.
Discovery combines every stem of a role with every registered kind extension.

When to Add a Stem
------------------

Add a stem when a project ships an asset under a filename that the default stems do not cover.

- A ``page`` stem so that ``page.css`` is picked up alongside ``template.css``.
- A ``vendor`` stem for third party assets inside a component folder.

Registering a Stem
------------------

Register stems in ``AppConfig.ready``.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig
   from next.static.discovery import default_stems

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_stems.register("template", "page")
           default_stems.register("component", "vendor")

After the registration, discovery picks up ``page.css`` and ``page.js`` next to ``template.djx``, and ``vendor.css`` inside a component folder.

The ``register`` method takes the role and the new stem.
The role is created when it does not exist.
A repeated registration of the same stem is a no op.

.. warning::

   Only the three built-in roles — ``template``, ``layout``, and ``component`` — are probed by discovery.
   Registering a stem under a new role name has no effect on asset collection.

Stem and Kind Interaction
-------------------------

A new stem participates in every registered kind, so a registration combines with every kind extension automatically.
See :doc:`/content/howto/add-a-custom-stem` for a worked example, and :doc:`asset-kinds` for pairing a stem with a custom kind.

Owner Resolution
----------------

A stem does not change ownership.
A ``vendor.css`` inside a component folder is still owned by the component.
A ``page.css`` next to ``template.djx`` is still owned by the page.

The owner determines when the collector adds the asset.

Common Patterns
---------------

Alternative Page Filename
~~~~~~~~~~~~~~~~~~~~~~~~~

Register a ``page`` stem under the ``template`` role so the page asset can be named ``page.css`` to match the ``page.py`` module.

Vendor Assets per Component
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Register a ``vendor`` stem under the ``component`` role for third party files that ship next to the component that depends on them.

See Also
--------

.. seealso::

   :doc:`co-located-files` for the default stem mapping.
   :doc:`asset-kinds` for the kind side of the pair.
   :doc:`/content/howto/add-a-custom-stem` for a recipe.
