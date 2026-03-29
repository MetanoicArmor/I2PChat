"""
Ensure real Pillow is in sys.modules before unittest-style tests that stub PIL.

Several tests use ``if "PIL" not in sys.modules:`` to inject a minimal fake for
environments without Pillow. In a full pytest run that made ``PIL`` a stub
without ``Image.new``, breaking image validation tests that need the real library.
"""

try:
    import PIL.Image  # noqa: F401
except ImportError:
    pass
