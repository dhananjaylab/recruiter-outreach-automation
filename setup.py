"""Thin setup.py shim.

All real configuration lives in pyproject.toml.  This file exists so that
`pip install -e .` and `python setup.py develop` work in older toolchains,
per the Hitchhiker's Guide to Python recommendation to keep setup.py at
the project root.

Reference: https://docs.python-guide.org/writing/structure/#setup-py
"""

from setuptools import setup

if __name__ == "__main__":
    setup()
