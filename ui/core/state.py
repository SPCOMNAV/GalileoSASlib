"""
UI State Management
-------------------
Centralized state for the UI layer. No internal dependencies except types.
"""

import copy
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Deque

from .types import WaitState


class ResultsCache:
    """
    Centralized cache for block execution results.
    
    Thread-safe singleton for storing UI-side results from block executions.
    """
    
    def __init__(self):
        self._results: Dict[str, Dict[str, Any]] = {}
        self._execution_ids: Dict[str, Optional[str]] = {}
    
    def set_result(
        self, 
        block_id: str, 
        payload: Dict[str, Any], 
        execution_id: Optional[str] = None
    ) -> None:
        """Store a result for a block."""
        payload = payload or {}
        self._results[block_id] = copy.deepcopy(payload)
        
        if execution_id is None:
            execution_id = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        
        self._execution_ids[block_id] = execution_id
        self._results[block_id].setdefault("executed_at", execution_id)
    
    def get_result(self, block_id: str) -> Dict[str, Any]:
        """Get a copy of the cached result for a block."""
        return copy.deepcopy(self._results.get(block_id, {}))
    
    def get_execution_id(self, block_id: str) -> Optional[str]:
        """Get the execution ID for a block."""
        return self._execution_ids.get(block_id)
    
    def has_result(self, block_id: str) -> bool:
        """Check if there's a cached result for a block."""
        return block_id in self._results
    
    def clear(self, block_id: Optional[str] = None) -> None:
        """Clear cache for a specific block or all blocks."""
        if block_id:
            self._results.pop(block_id, None)
            self._execution_ids.pop(block_id, None)
        else:
            self._results.clear()
            self._execution_ids.clear()


class LogManager:
    """
    Centralized log management for the UI.
    
    Forwards logs to PipelineState so all logs appear in one place.
    """
    
    def __init__(self):
        self._wait_state: WaitState = {"active": False, "message": ""}
        self._pipeline_state = None  # Lazy loaded to avoid circular imports
    
    def _get_pipeline_state(self):
        """Lazy load PipelineState to avoid circular imports."""
        if self._pipeline_state is None:
            from SASpipeline import get_pipeline_state
            self._pipeline_state = get_pipeline_state()
        return self._pipeline_state
    
    def append(self, message: str) -> None:
        """Add a log message - forwards to PipelineState."""
        self._get_pipeline_state().append_log(message)
    
    def get_logs(self) -> list:
        """Get all logs from PipelineState."""
        return self._get_pipeline_state().get_logs()
    
    def clear(self) -> None:
        """Clear is handled by PipelineState.start()."""
        pass  # PipelineState.start() clears logs
    
    def sync_wait_state(self) -> Optional[WaitState]:
        """
        Sync wait state from log messages.
        Returns new state if changed, None otherwise.
        """
        state: WaitState = {"active": False, "message": ""}
        logs = self.get_logs()
        
        for entry in reversed(logs):
            if "[WAIT-END]" in entry:
                state = {"active": False, "message": ""}
                break
            if "[WAIT-START]" in entry:
                message = entry.split("[WAIT-START]", 1)[1].strip()
                state = {"active": True, "message": message}
                break
        
        if state != self._wait_state:
            self._wait_state = state
            return state
        return None
    
    @property
    def wait_state(self) -> WaitState:
        """Current wait state."""
        return self._wait_state


# -------------------------------------------------------------------------
#  SINGLETON INSTANCES
# -------------------------------------------------------------------------
results_cache = ResultsCache()
log_manager = LogManager()
