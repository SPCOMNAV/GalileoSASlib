"""
Callback Registration
---------------------
Central point for registering all callbacks.
"""

from dash import Dash


def register_all_callbacks(app: Dash) -> None:
    """
    Register all callbacks with the Dash app.
    
    Args:
        app: The Dash application instance
    """
    from . import pipeline_cb
    from . import modal_cb
    from . import graph_cb
    
    pipeline_cb.register_callbacks(app)
    modal_cb.register_callbacks(app)
    graph_cb.register_callbacks(app)
