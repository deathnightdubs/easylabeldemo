"""The Qt interface.

This is the only package that imports a GUI toolkit. Everything it does routes through
:class:`numis.services.CollectionService`, so the same logic is exercised by the tests and by
the command line.
"""
