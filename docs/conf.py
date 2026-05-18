import os
import sys
from pathlib import Path

# add the project root to the Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# configure Django settings for documentation
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django.conf.global_settings')

import django
from django.conf import settings

# minimal Django settings for documentation
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            'django.contrib.contenttypes',
            'django.contrib.auth',
            'django.contrib.staticfiles',
            'next',
        ],
        TEMPLATES=[
            {
                'BACKEND': 'django.template.backends.django.DjangoTemplates',
                'APP_DIRS': True,
                'OPTIONS': {
                    'builtins': ['next.templatetags.forms'],
                },
            },
        ],
        USE_TZ=True,
        SECRET_KEY='dummy-key-for-docs',
        STATIC_URL='/static/',
    )
    django.setup()


# project information
project = "next.dj"
copyright = "2025-2026, paqstd-dev"
author = "paqstd-dev"

# general configuration
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.doctest",
    "sphinx.ext.intersphinx",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.githubpages",
    "myst_parser",
    "sphinx_design",
    "sphinx_copybutton",
    "sphinxcontrib.mermaid",
    "sphinx_reredirects",
]

# mermaid configuration
mermaid_output_format = "raw"
mermaid_init_js = "mermaid.initialize({startOnLoad:true, theme:'default'});"

# autodoc configuration
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__,staticfiles_storage",
}

# autosummary configuration
autosummary_generate = True

# napoleon configuration for Google/NumPy style docstrings
napoleon_google_docstring = True
napoleon_numpy_docstring = True
napoleon_include_init_with_doc = False
napoleon_include_private_with_doc = False
napoleon_include_special_with_doc = True

# intersphinx mapping for external references
intersphinx_mapping = {
    "python": ("https://docs.python.org/3/", None),
    "django": ("https://docs.djangoproject.com/en/stable/", "https://docs.djangoproject.com/en/stable/_objects/"),
}

# templates path
templates_path = ["_templates"]

# list of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files
exclude_patterns = [
    "_build",
    "Thumbs.db",
    ".DS_Store",
]

# 404 page is intentionally not in toctree
# autodoc cross-references to stdlib types (Path, HttpRequest, etc.) generate noisy
# warnings that we do not want to treat as errors during this phase of the rewrite
suppress_warnings = ["toc.not_included", "ref.class", "autodoc"]

# URL redirects for moved pages (sphinx-reredirects)
redirects = {
    "content/guide/getting-started": "../intro/install.html",
    "content/guide/file-router": "../topics/file-router.html",
    "content/guide/pages-and-templates": "../topics/pages.html",
    "content/guide/components": "../topics/components.html",
    "content/guide/context": "../topics/context.html",
    "content/guide/forms": "../topics/forms/index.html",
    "content/guide/dependency-injection": "../topics/dependency-injection.html",
    "content/guide/static-assets": "../topics/static-assets/index.html",
    "content/guide/autoreload": "../internals/autoreload.html",
    "content/guide/testing": "../topics/testing.html",
    "content/guide/project-layout": "../topics/project-layout.html",
    "content/guide/extending": "../topics/extending.html",
    "content/api/reference": "../ref/index.html",
    "content/api/pages": "../ref/pages.html",
    "content/api/components": "../ref/components.html",
    "content/api/urls": "../ref/urls.html",
    "content/api/forms": "../ref/forms.html",
    "content/api/static": "../ref/static.html",
    "content/api/deps": "../ref/deps.html",
    "content/api/server": "../ref/server.html",
    "content/api/testing": "../ref/testing.html",
    "content/api/signals": "../ref/signals.html",
    "content/api/conf": "../ref/conf.html",
    "content/api/apps": "../ref/apps.html",
    "content/reference/system-checks": "../ref/system-checks.html",
    "content/contributing/documentation-guide": "writing-documentation.html",
}

# the name of the Pygments (syntax highlighting) style to use
# Shibuya derives both light and dark code styles from this base
pygments_style = "github-light-default"

# html theme (Shibuya)
html_theme = "shibuya"
html_theme_options = {
    "accent_color": "indigo",
    "color_mode": "auto",
    "dark_code": True,
    "page_layout": "default",
    "light_logo": "_static/img/logos/next-dj-wordmark-light.svg",
    "dark_logo": "_static/img/logos/next-dj-wordmark-dark.svg",
    "github_url": "https://github.com/next-dj/next-dj",
    "globaltoc_expand_depth": 2,
    "toctree_collapse": False,
    "nav_links": [
        {"title": "Issues", "url": "https://github.com/next-dj/next-dj/issues", "external": True},
        {"title": "Discussions", "url": "https://github.com/orgs/next-dj/discussions", "external": True},
    ],
}

# html static files
html_static_path = ["_static"]
html_css_files = ["css/tokens.css", "css/theme.css", "css/landing.css"]
html_js_files = ["js/landing.js"]
html_favicon = "_static/img/favicon.svg"

# ensure _static and _static/images exist
import os
static_dir = os.path.join(os.path.dirname(__file__), "_static")
if not os.path.exists(static_dir):
    os.makedirs(static_dir)
# html help
htmlhelp_basename = "nextdjdoc"

# latex options
latex_elements = {
    "papersize": "letterpaper",
    "pointsize": "10pt",
    "preamble": "",
    "figure_align": "htbp",
}

latex_documents = [
    (
        "index",
        "nextdj.tex",
        "next.dj Documentation",
        "paqstd-dev",
        "manual",
    ),
]

# man pages
man_pages = [
    (
        "index",
        "nextdj",
        "next.dj Documentation",
        [author],
        1,
    )
]

# texinfo
texinfo_documents = [
    (
        "index",
        "nextdj",
        "next.dj Documentation",
        author,
        "nextdj",
        "Next generation Django framework",
        "Miscellaneous",
    ),
]

# epub options
epub_title = project
epub_author = author
epub_publisher = author
epub_copyright = copyright

epub_exclude_files = ["search.html"]

# myst parser configuration
myst_enable_extensions = [
    "colon_fence",
    "deflist",
    "dollarmath",
    "html_admonition",
    "html_image",
    "replacements",
    "smartquotes",
    "substitution",
    "tasklist",
]

# source suffix
source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}
