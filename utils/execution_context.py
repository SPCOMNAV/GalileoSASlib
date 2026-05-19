"""
execution_context.py
--------------------
Per-execution metadata and path helpers, kept as a process-wide singleton so
block code can pull execution-scoped paths without having to be threaded a
context object through every function call.

The runtime block-to-block data flow lives in ``utils.executor.Executor.ctx``
and ``Executor.globals`` — this module is intentionally NOT used for that any
more. Its remaining responsibilities are:

* ``execution_id``        — set once per pipeline run by PipelineRunner.
* ``get_execution_results_dir(subdir)``   →  ``executions/<id>/<subdir>/``
* ``get_execution_downloads_dir(subdir)`` →  ``executions/<id>/downloads/<subdir>/``
* ``get/set/has/clear`` over a generic dict (kept for ad-hoc UI callbacks).

Usage:
    from utils.execution_context import ExecutionContext
    ctx = ExecutionContext()
    ctx.set_execution_id("20260505_120000")
    out_dir = ctx.get_execution_results_dir("ECS")
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional


class ExecutionContext:
    """
    Singleton for execution-time state between blocks.
    
    NOT for configuration - use block JSONs for that.
    This is for passing runtime data between blocks during a pipeline execution.
    """
    
    _instance: Optional["ExecutionContext"] = None
    _lock: Lock = Lock()
    _initialized: bool = False
    
    def __new__(cls) -> "ExecutionContext":
        """Create singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self) -> None:
        """Initialize the context (only once)."""
        if not ExecutionContext._initialized:
            with ExecutionContext._lock:
                if not ExecutionContext._initialized:
                    self._data: Dict[str, Any] = {}
                    self._execution_id: Optional[str] = None
                    ExecutionContext._initialized = True
    
    def clear(self) -> None:
        """Clear all execution data. Call at start of new pipeline run."""
        with self._lock:
            self._data.clear()
            self._execution_id = None
    
    def set_execution_id(self, execution_id: str) -> None:
        """Set the current execution ID."""
        with self._lock:
            self._execution_id = execution_id
    
    def get_execution_id(self) -> Optional[str]:
        """Get the current execution ID."""
        with self._lock:
            return self._execution_id
    
    def get_execution_results_dir(self, subdir: Optional[str] = None) -> Path:
        """
        Get the results directory for the current execution.
        
        Creates: executions/{execution_id}/{subdir}/
        
        Args:
            subdir: Optional subdirectory (e.g., "ECS", "plots", "authentication")
        
        Returns:
            Path to the execution results directory
        """
        from utils.utils import BASE_DIR
        
        execution_id = self.get_execution_id() or "unknown"
        base_dir = BASE_DIR / "executions" / execution_id
        
        if subdir:
            result_dir = base_dir / subdir
        else:
            result_dir = base_dir
        
        result_dir.mkdir(parents=True, exist_ok=True)
        return result_dir
    
    def get_execution_downloads_dir(self, subdir: Optional[str] = None) -> Path:
        """
        Get the downloads directory for the current execution.
        
        Creates: executions/{execution_id}/downloads/{subdir}/
        
        Args:
            subdir: Optional subdirectory (e.g., "RECS", "BGD", "SLOG")
        
        Returns:
            Path to the execution downloads directory
        """
        from utils.utils import BASE_DIR
        
        execution_id = self.get_execution_id() or "unknown"
        base_dir = BASE_DIR / "executions" / execution_id / "downloads"
        
        if subdir:
            download_dir = base_dir / subdir
        else:
            download_dir = base_dir
        
        download_dir.mkdir(parents=True, exist_ok=True)
        return download_dir
    
    def set(self, key: str, value: Any) -> None:
        """Store a value for use by other blocks."""
        with self._lock:
            self._data[key] = value
    
    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve a value, with optional default."""
        with self._lock:
            return self._data.get(key, default)
    
    def has(self, key: str) -> bool:
        """Check if a key exists."""
        with self._lock:
            return key in self._data
    
    def remove(self, key: str) -> None:
        """Remove a key if it exists."""
        with self._lock:
            self._data.pop(key, None)
    
    def keys(self) -> List[str]:
        """Get all stored keys."""
        with self._lock:
            return list(self._data.keys())
    
    def to_dict(self) -> Dict[str, Any]:
        """Get a copy of all stored data."""
        with self._lock:
            return dict(self._data)
    

# ─────────────────────────────────────────────────────────────────────────────
# Module-level convenience function
# ─────────────────────────────────────────────────────────────────────────────

def get_execution_context() -> ExecutionContext:
    """Get the ExecutionContext singleton instance."""
    return ExecutionContext()


__all__ = [
    "ExecutionContext",
    "get_execution_context",
]
