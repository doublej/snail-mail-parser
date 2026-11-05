"""Sphinx configuration file."""

import os
import sys

sys.path.insert(0, os.path.abspath('..'))

# Project information
project = 'Snail Mail Parser'
copyright = '2024, Jurre Jan'
author = 'Jurre Jan'
release = '0.1.0'

# General configuration
extensions = [
    'sphinx.ext.autodoc',
    'sphinx.ext.viewcode',
    'sphinx.ext.napoleon',
]

templates_path = ['_templates']
exclude_patterns = ['_build', 'Thumbs.db', '.DS_Store']

# HTML output options
html_theme = 'alabaster'
html_static_path = ['_static']