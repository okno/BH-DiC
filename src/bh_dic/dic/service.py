"""Compatibility import for the mandatory DIC service package boundary.

The implementation remains in :mod:`bh_dic.services.dic_service`; this module avoids a
second service implementation while preserving the repository structure and public import.
"""

from bh_dic.services.dic_service import DicService

__all__ = ["DicService"]
