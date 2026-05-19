"""
UI Type Definitions
-------------------
Shared types for the UI layer. No internal dependencies.
"""

from typing import TypedDict, Optional, List, Dict, Any


class ClickData(TypedDict, total=False):
    """Data from a ReactFlow node click."""
    node_id: str
    timestamp: float
    node: Optional[Dict[str, Any]]


class PipelineResult(TypedDict, total=False):
    """Result from a pipeline operation."""
    success: bool
    data: Optional[Dict[str, Any]]
    error: Optional[str]
    message: str


class ModalState(TypedDict, total=False):
    """State of a modal dialog."""
    is_open: bool
    block_id: Optional[str]
    title: str
    content: Any


class WaitState(TypedDict):
    """State for wait/loading indicators."""
    active: bool
    message: str
