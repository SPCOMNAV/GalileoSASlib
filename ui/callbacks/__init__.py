"""
UI Callbacks Module
-------------------
Dash callbacks organized by functionality.
Depends on all other layers.
"""

from .registration import register_all_callbacks

__all__ = ["register_all_callbacks"]
