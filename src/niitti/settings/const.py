"""
Re-export of the shared application constants.

The constants moved to :mod:`niitti.const` so that :mod:`niitti.paths` can read them without an import of the
settings subpackage, which needs pydantic. This module stays for the existing import path.
"""

from niitti.const import DEFAULT_APP_AUTHOR, DEFAULT_APP_NAME

__all__ = ["DEFAULT_APP_AUTHOR", "DEFAULT_APP_NAME"]
