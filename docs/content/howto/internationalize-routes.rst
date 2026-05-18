.. _howto-internationalize-routes:

Internationalize Routes
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

Enable i18n in Settings
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

Add the Locale Middleware
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

Mount the Router Under a Language Prefix
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

Translate In-Page Text
~~~~~~~~~~~~~~~~~~~~~~

Mark template strings with the standard Django i18n tags.
Load the ``i18n`` tag library at the top of the template.

.. code-block:: jinja
   :caption: shop/pages/template.djx

   {% load i18n %}

   <h1>{% translate "Welcome to the shop" %}</h1>
   <p>{% blocktranslate %}Browse the catalog below.{% endblocktranslate %}</p>

Translate text built in Python with :func:`~django.utils.translation.gettext`.
A ``@context`` callable returns the already-translated string.

.. code-block:: python
   :caption: shop/pages/page.py

   from django.utils.translation import gettext as _

   from next.pages import context


   @context("heading")
   def heading() -> str:
       """Return the localized page heading."""
       return _("Featured products")

Compile the Catalogs
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

See Also
--------

.. seealso::

   :doc:`/content/topics/file-router` for how the router include is mounted.
   :doc:`/content/howto/reverse-urls` for building prefixed URLs from code.
