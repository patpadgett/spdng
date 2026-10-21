"""The version of spdng.

It lives in its own module so that both the package and the CLI can read
it without a circular import. The package's `__init__` imports the CLI's
`main`, and the CLI needs the version, so putting the constant in either
of those two creates a cycle.
"""
__version__ = "0.5.0"
