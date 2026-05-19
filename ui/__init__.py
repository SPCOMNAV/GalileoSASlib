"""
SAS Dashboard UI
----------------

Usage:
    # Run directly
    cd SASlib && python -m ui.app
    # or
    cd SASlib && python ui/app.py
    
    # Or import
    from ui import app, server, run
    
Architecture:
    ui/
    ├── core/        # Base layer (types, state, config)
    ├── services/    # Service layer (pipeline wrapper, persistence)
    ├── components/  # Visual components (graph, modals, plots)
    ├── callbacks/   # Dash callbacks
    └── app.py       # Main application

Dependency Flow:
    callbacks → components → services → core → SASpipeline (external)
"""

from ui.app import app, server, run

__all__ = ["app", "server", "run"]
