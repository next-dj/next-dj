.. _ref-scripts:

Scripts and consent reference
=============================

Module summary
--------------

``next.scripts`` discovers the ``scripts.py`` of every page tree and plans which scripts a render writes into the head and which it hands the runtime.
``next.consent`` reads the visitor's consent per category through a pluggable backend and injects it as a ``Consent`` parameter.
:doc:`/content/topics/scripts/index` is the guide.

Scripts
-------

``Script`` is one third-party script and ``Strategy`` the moment it loads.
A script runs in the order its ``scripts.py`` declares it, and ``ALLOWED_ATTRS`` with any ``data-*`` name is what ``attrs`` may carry.
``ScriptsSourceImportError`` names a ``scripts.py`` that failed to import.

.. automodule:: next.scripts.markers
   :members: Script, Strategy, HEAD_STRATEGIES, ALLOWED_ATTRS, SCRIPT_ATTR

.. automodule:: next.scripts.errors
   :members:

The manager
~~~~~~~~~~~

``scripts_manager`` loads every tree's ``scripts.py`` once, walking the routed page trees of the router manager, and again when its mtime moves under ``DEBUG``, and ``render`` answers the head tags and the reserved payload entries of one render.
The source loads through ``load_tree_source`` of ``next.utils``, the loader the SEO sources share.
A ``ScriptsManager`` built without a registry keeps one of its own.
``forget_scripts`` is the receiver ``settings_reloaded`` and ``router_reloaded`` drop every discovered tree through, and ``next.testing.reset_scripts`` calls it for a test.
The next render walks the trees again and executes only the ``scripts.py`` files whose mtime moved, the rest kept as read.
A client-rendered ``{% #consented %}`` body leaves a ``GatedNote`` for each script it holds back, and ``render`` turns them into manifest entries in the category of the block.
The static injector reaches it through the ``PageScripts`` port of :doc:`ports`, so ``next.static`` never imports ``next.scripts``.

.. automodule:: next.scripts.manager
   :members: ScriptsManager, GatedNote, scripts_manager, forget_scripts

Consent
-------

``Consent`` is the frozen value a visitor's choice reads as, ``granted`` holding ``necessary`` always and ``decided`` staying false until the visitor chooses.
``allows(category)`` answers one category, and a template reads ``consent.marketing`` through ``__getitem__``.
``get_consent(request)`` answers the consent of a request, read once and kept to the configured categories, and ``consent_categories()`` the configured list with ``necessary`` first.

.. automodule:: next.consent.markers
   :members:

.. autofunction:: next.consent.get_consent

.. autofunction:: next.consent.consent_categories

Backends
~~~~~~~~

A ``ConsentBackend`` takes its whole ``CONSENT`` entry, exposes ``OPTIONS`` as ``options``, and reads a ``Consent`` off a request, which is the one method a subclass implements.
``client_config()`` answers the entries the backend adds to ``$consent``, nothing by default, and the categories and the choice win over an entry of the same name.
The consent manager reads ``client_config()`` once per backend instance, so its entries depend on the settings alone.
The runtime keeps the choice only in its consent cookie, so a backend that reads something else gets nothing back from the browser.
``CookieConsentBackend`` reads the ``2:<a>|<b>:<seconds>`` cookie the runtime writes and also accepts the ``1:<a>,<b>:<seconds>`` form, whose categories are separated by commas.
Its ``cookie()`` answers the name, age, and flags the runtime writes the cookie with, which is the ``cookie`` entry its ``client_config()`` adds.
Under a backend that adds no ``cookie`` entry, the runtime keeps its cookie of the choice under the default name and age.
A backend that raises or answers anything but a ``Consent`` reads as ``UNDECIDED`` and is logged at most once every ten minutes, and under ``DEBUG`` or ``STRICT_LOADING`` the exception propagates.

.. automodule:: next.consent.backends
   :members:

Injection
~~~~~~~~~

``ConsentProvider`` fills a parameter annotated ``Consent`` and follows ``server_mode(request)`` of ``next.consent.manager``, the one answer the tags and the scripts render read as well.
Where the server does not read the cookie, a shared page under ``"auto"`` and every page under ``SERVER_RENDER = False``, it answers ``UNDECIDED``.
Every other read marks the response ``Vary: Cookie``, and under ``True`` a shared page is then sent with ``Cache-Control: private``.

.. automodule:: next.consent.providers
   :members:

Checks
------

``next.scripts.checks`` reads every ``scripts.py`` and the ``{% #consented %}`` blocks of the composed pages, and ``next.consent.checks`` the ``CONSENT`` scope, the category list included.
``next.static.checks`` owns the checks about the CSP nonce, and :doc:`system-checks` lists every code.

.. automodule:: next.scripts.checks
   :members:

.. automodule:: next.consent.checks
   :members:

Signals
-------

``scripts_registered`` is sent when the registry takes a tree's ``scripts.py``, with the ``root``, ``source``, and ``scripts`` keyword arguments and the ``ScriptsRegistry`` class as sender.
A tree without a ``scripts.py`` sends nothing.
``consent_backend_loaded`` is sent when the consent backend is built, with the ``config`` and ``instance`` keyword arguments.

.. automodule:: next.scripts.signals
   :members:
   :no-index:

.. automodule:: next.consent.signals
   :members:
   :no-index:

See also
--------

.. seealso::

   :doc:`/content/topics/scripts/index` for the guides.
   :doc:`/content/howto/write-a-vendor-adapter` for a vendor built on ``Script``.
   :doc:`client-extras` for the browser side.
   :doc:`settings` for ``CONSENT`` and ``CSP_NONCE``.
   :doc:`template-tags` for ``{% script %}``, ``{% #consented %}``, and ``{% collect_head %}``.
