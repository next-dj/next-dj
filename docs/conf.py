"""Configuration file for the Sphinx documentation builder.

This file contains the configuration for generating documentation
for the next.dj framework using Sphinx and Read the Docs.
"""

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
            'next',
        ],
        NEXT_PAGES=[
            {
                'BACKEND': 'next.urls.FileRouterBackend',
                'APP_DIRS': True,
                'OPTIONS': {},
            },
        ],
        USE_TZ=True,
        SECRET_KEY='dummy-key-for-docs',
    )
    django.setup()

# project information
project = "next.dj"
copyright = "2025, paqstd-dev"
author = "paqstd-dev"
release = "0.1.0"

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
]

# autodoc configuration
autodoc_default_options = {
    "members": True,
    "member-order": "bysource",
    "special-members": "__init__",
    "undoc-members": True,
    "exclude-members": "__weakref__",
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
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

# the name of the Pygments (syntax highlighting) style to use
pygments_style = "sphinx"

# html theme options
html_theme = "sphinx_rtd_theme"
html_theme_options = {
    "analytics_id": "",  # provided by Google Analytics
    "logo_only": False,
    "prev_next_buttons_location": "bottom",
    "style_external_links": False,
    "vcs_pageview_mode": "",
    "style_nav_header_background": "#2980B9",
    # toc options
    "collapse_navigation": True,
    "sticky_navigation": True,
    "navigation_depth": 4,
    "includehidden": True,
    "titles_only": False,
}

# html static files
html_static_path = ["_static"]

# ensure _static directory exists
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
source_suffix = [".rst", ".md"]
