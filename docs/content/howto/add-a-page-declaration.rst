.. _howto-add-a-page-declaration:

Add a page declaration
======================

Problem
-------

Every page of a shop belongs to a team, and the log line of each render should name the team that owns the page.
The owner belongs next to the page, as ``page.transaction()`` and ``page.router()`` do, and a section should name it once for every page below it.

Solution
--------

A ``PageDeclaration`` names one call, the function that parses its arguments, the value a page without the call reads, and how the value reaches the pages below.
The app that owns it registers it in ``AppConfig.ready()``, a ``page.py`` calls it at its top level, and any code that knows the path of a page reads the value with ``of()``.

Walkthrough
-----------

Describe the call
~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: shop/declarations.py

   from next.pages.declarations import Inherit, PageDeclaration

   def _team(name: str) -> str:
       if not name.isidentifier():
           msg = f"the team must be an identifier, got {name!r}"
           raise ValueError(msg)
       return name


   owner = PageDeclaration("owner", _team, "platform", inherit=Inherit.NEAREST)

The parse function takes the arguments of the call and returns the value, and its signature is the signature of the call, so a type checker and an IDE read it.
A ``ValueError`` it raises becomes an :exc:`~django.core.exceptions.ImproperlyConfigured` naming the ``page.py`` when the module runs.
``Inherit.NEAREST`` gives each page the value of the closest ``page.py`` at or above it that makes the call, ``Inherit.OWN`` binds the declaring page alone, and ``Inherit.MERGE`` folds every value from the root down through a ``merge`` function.

Register it
~~~~~~~~~~~

.. code-block:: python
   :caption: shop/apps.py

   from django.apps import AppConfig
   from shop.declarations import owner

   from next.pages.declarations import register_declaration

   class ShopConfig(AppConfig):
       name = "shop"

       def ready(self) -> None:
           register_declaration(owner)

A call to a declaration that is not registered raises, and a second declaration under a taken name raises at registration.

Declare it on a section
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: pages/checkout/page.py

   from shop.declarations import owner

   owner("checkout")

   template = "<h1>Checkout</h1>"

Every page under ``pages/checkout/`` now reads ``"checkout"``, unless its own ``page.py`` or one between calls ``owner()`` again.
The last call of a module wins, a call inside a function raises ``DeclarationOutsideModuleError``, and a reload of the module drops a call the edit removed.
A call outside a ``page.py`` declares nothing, and the owning app reports it from a system check through ``owner.outside()``.

Read it
~~~~~~~

.. code-block:: python
   :caption: shop/signals.py

   import logging

   from django.dispatch import receiver
   from shop.declarations import owner

   from next.pages.signals import page_rendered

   logger = logging.getLogger("shop.owners")


   @receiver(page_rendered)
   def log_owner(sender, file_path, **kwargs) -> None:
       logger.info("rendered %s for %s", file_path, owner.of(file_path))

``owner.of(file_path)`` loads the page modules it needs once per reload and answers from a memo afterwards, and a project without a single call answers the default at once.

Verification
------------

.. code-block:: python
   :caption: tests/test_owner.py

   from pathlib import Path

   from shop.declarations import owner

   def test_a_section_names_the_owner_of_its_pages(tmp_path: Path) -> None:
       pay = tmp_path / "checkout" / "pay"
       pay.mkdir(parents=True)
       (tmp_path / "checkout" / "page.py").write_text(
           "from shop.declarations import owner\n\nowner('checkout')\n"
       )
       (pay / "page.py").write_text("template = '<p>pay</p>'\n")
       (tmp_path / "page.py").write_text("template = '<p>home</p>'\n")
       assert owner.of(pay / "page.py") == "checkout"
       assert owner.of(tmp_path / "page.py") == "platform"

See also
--------

.. seealso::

   :doc:`/content/ref/pages` for the registry and the four declarations next.dj ships.
