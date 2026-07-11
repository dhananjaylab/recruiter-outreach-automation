# docs/conf.py — Sphinx configuration
# Reference: https://docs.python-guide.org/writing/structure/#documentation

import os
import sys

# Make sure autodoc can find the package at the project root.
sys.path.insert(0, os.path.abspath(".."))

project   = "Recruiter Outreach Automation"
author    = "Dhananjay Lokhande"
copyright = "2024, Dhananjay Lokhande"
release   = "2.0.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.viewcode",
    "sphinx.ext.napoleon",
]

html_theme = "alabaster"
