.. _howto-internationalize-routes:

Internationalize routes
=======================

Problem
-------

The page tree should serve several languages, each under its own URL prefix such as ``/en/`` and ``/de/``, and the in-page text must follow the active language.

Solution
--------

The file router mounts as a single :func:`~django.urls.include` in the root URLconf.
Wrap that include in :func:`~django.conf.urls.i18n.i18n_patterns` so Django prepends the language prefix to every routed URL.
Add :class:`~django.middleware.locale.LocaleMiddleware` so the prefix sets the active language, and translate template text with the usual Django i18n tags.

Walkthrough
-----------

Enable i18n in settings
~~~~~~~~~~~~~~~~~~~~~~~

Turn on :doc:`translation <django:topics/i18n/translation>`, list the offered languages, and point at a locale directory.

.. code-block:: python
   :caption: config/settings.py

   USE_I18N = True

   LANGUAGE_CODE = "en"

   LANGUAGES = [
       ("en", "English"),
       ("de", "Deutsch"),
   ]

   LOCALE_PATHS = [BASE_DIR / "locale"]

Add the locale middleware
~~~~~~~~~~~~~~~~~~~~~~~~~

Place :class:`~django.middleware.locale.LocaleMiddleware` after the session middleware and before :class:`~django.middleware.common.CommonMiddleware`.
It reads the URL prefix and sets the active language for the request.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.locale.LocaleMiddleware",
       "django.middleware.common.CommonMiddleware",
       "django.middleware.csrf.CsrfViewMiddleware",
       "django.contrib.auth.middleware.AuthenticationMiddleware",
       "django.contrib.messages.middleware.MessageMiddleware",
       "django.middleware.clickjacking.XFrameOptionsMiddleware",
   ]

Mount the router under a language prefix
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wrap the file router include in :func:`~django.conf.urls.i18n.i18n_patterns`.
Every routed URL now resolves under ``/en/`` and ``/de/``, and the prefix-free URLs redirect to the active language.

.. code-block:: python
   :caption: config/urls.py

   from django.conf.urls.i18n import i18n_patterns
   from django.urls import include, path

   urlpatterns = i18n_patterns(
       path("", include("next.urls")),
   )

Keep URLs that must not carry a prefix, such as a health check, in a plain ``urlpatterns`` list outside the ``i18n_patterns`` call.

Translate in-page text
~~~~~~~~~~~~~~~~~~~~~~

Mark template strings with the standard Django i18n tags.
Load the ``i18n`` tag library at the top of the template.
The examples below use a separate ``shop`` project rather than the Notes project shown elsewhere in the documentation.

.. code-block:: jinja
   :caption: shop/routes/template.djx

   {% load i18n %}

   <h1>{% translate "Welcome to the shop" %}</h1>
   <p>{% blocktranslate %}Browse the catalog below.{% endblocktranslate %}</p>

Translate text built in Python with :func:`~django.utils.translation.gettext`.
A ``@context`` callable returns the already-translated string.

.. code-block:: python
   :caption: shop/routes/page.py

   from django.utils.translation import gettext as _

   from next import context

   @context("heading")
   def heading() -> str:
       """Return the localized page heading."""
       return _("Featured products")

Translate page titles
~~~~~~~~~~~~~~~~~~~~~

Page titles and descriptions live in the ``metadata`` dict of a ``page.py``, and a lazy translation stays lazy until ``{% metadata %}`` renders it under the language of the request.
Wrap the texts in :func:`~django.utils.translation.gettext_lazy`, the settings tier included, and add ``alternates`` so every variant of a page links to the others.

.. code-block:: python
   :caption: shop/routes/page.py

   from django.utils.translation import gettext_lazy as _

   metadata = {
       "title": _("Featured products"),
       "description": _("The products the shop features this week."),
       "canonical": True,
       "alternates": {"languages": True},
   }

``alternates.languages`` set to ``True`` walks ``LANGUAGES`` and translates the canonical URL through :func:`~django.urls.translate_url`, which is why the router include has to sit inside ``i18n_patterns`` and ``next.W087`` warns when it does not.
The page renders one ``<link rel="alternate" hreflang="...">`` per language and an ``x-default`` pointing at the ``LANGUAGE_CODE`` variant.
See :doc:`/content/topics/seo/social-and-canonical` for the mapping form and the ``x_default`` override.

The sitemap follows the same languages.
``i18n = True`` in the ``sitemap.py`` at the top of the page root lists every URL once per language with the prefix in the path, and ``alternates = True`` adds the hreflang block to each entry, see :doc:`/content/topics/seo/sitemaps`.
Because the router include sits inside ``i18n_patterns``, the sitemap and robots routes it carries sit under the prefix too, so ``path("", include("next.seo.urls"))`` goes in the plain ``urlpatterns`` list ahead of the language block to mount them at the host root, see :ref:`topics-seo-host-root`.

Compile the catalogs
~~~~~~~~~~~~~~~~~~~~

Extract the marked strings and compile the binary catalogs.

.. code-block:: bash
   :caption: shell

   uv run python manage.py makemessages -l de
   uv run python manage.py compilemessages

Edit ``locale/de/LC_MESSAGES/django.po`` with the German strings before compiling.

Verification
------------

Start the server and request the same route under two prefixes.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver

Visiting ``/en/`` renders the English text and ``/de/`` renders the German text.
A request to the prefix-free URL redirects to the language picked from the request.

Once the router include is wrapped in ``i18n_patterns``, ``page_reverse`` returns a URL carrying the active language prefix.
URL names are unchanged.
``page_reverse`` reverses the same ``next:page_<segments>`` name, and ``i18n_patterns`` applies the active prefix at resolve time.

See also
--------

.. seealso::

   :doc:`/content/topics/file-router` for how the router include is mounted.
   :doc:`/content/howto/reverse-urls` for building prefixed URLs from code.
   :doc:`/content/topics/seo/metadata` for the title template and lazy texts.
