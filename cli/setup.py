# Compatibility shim for older pip/setuptools that do not support
# the PEP 660 build_editable hook required for editable installs
# via pyproject.toml alone.  Having this file allows 'pip install -e .'
# to fall back to the classic setuptools editable mechanism on any
# pip/setuptools version.
from setuptools import setup

setup()
