"""Numismatic collection manager — core library.

This package contains no GUI code. Everything here is usable from a GUI, a CLI or
the test suite, which is the architectural rule the project depends on.

Package name ``numis`` is a placeholder until the product is named.
"""

__version__ = "0.1.0.dev0"

SCHEMA_VERSION = "0003"
"""Current schema revision.

**Bump this and add a migration in ``numis.migrations`` for every schema change.**
Changing the models without doing so means existing libraries will not open, which is
how a real collection gets locked out by an upgrade.
"""
