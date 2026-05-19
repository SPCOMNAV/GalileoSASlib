#!/usr/bin/env python
"""
SASgraphicInterface.py
----------------------
Launcher for the SAS (Satellite Authentication System) Dashboard UI.

This script starts the web-based graphical interface for configuring,
monitoring, and executing the OSNMA authentication pipeline.

Features:
    - Visual pipeline flow diagram with interactive blocks
    - Real-time configuration editing for each processing block
    - Pipeline execution control (start/stop/single block)
    - Live log monitoring and result visualization
    - Configuration import/export (JSON)

Architecture:
    SASgraphicInterface.py → ui/ → SASpipeline.py → modules/
    
    The UI communicates ONLY with SASpipeline.py, which orchestrates
    the execution of individual blocks (ConfigInit, RECS download,
    TESLA key management, Signal Correlation, Authentication, etc.)
Usage:
    python SASgraphicInterface.py
    
    Then open http://127.0.0.1:8050 in your browser.

Requirements:
    - dash, dash-bootstrap-components, dash-mantine-components
    - plotly, dash-diagram (ReactFlow)
    - See requirements.txt for full list
"""

from ui import run

if __name__ == "__main__":
    run()
