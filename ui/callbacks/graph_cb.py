"""
Graph Callbacks
---------------
Callbacks for ReactFlow graph interactions and updates.
"""

import copy

from dash import Dash, Input, Output, State, callback_context, no_update

from ui.core.state import log_manager
from ui.components.graph import make_nodes
from SASpipeline import get_pipeline_state, PIPELINE_ORDER


def register_callbacks(app: Dash) -> None:
    """Register graph-related callbacks."""
    
    # Clientside callback for detecting clicks on ReactFlow nodes
    # Uses a global listener on document to capture all clicks
    app.clientside_callback(
        """
        function(n_intervals) {
            // Initialize global click listener only once
            if (!window._sasClickListenerInitialized) {
                window._sasClickListenerInitialized = true;
                window._sasLastClickData = null;
                
                document.addEventListener('click', function(e) {
                    // Find if click was on a ReactFlow node
                    var nodeElement = e.target.closest('.react-flow__node');
                    if (nodeElement) {
                        var nodeId = nodeElement.getAttribute('data-id');
                        if (nodeId) {
                            window._sasLastClickData = {
                                node_id: nodeId,
                                timestamp: Date.now()
                            };
                        }
                    }
                }, true);
            }
            
            // Return click data if available
            if (window._sasLastClickData) {
                var data = window._sasLastClickData;
                window._sasLastClickData = null;
                return data;
            }
            
            return window.dash_clientside.no_update;
        }
        """,
        Output("click-with-timestamp", "data"),
        Input("click-detector", "n_intervals"),
        prevent_initial_call=True,
    )
    
    @app.callback(
        Output("rf", "nodes"),
        Input("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def update_reactflow_nodes(blocks_data):
        """Update ReactFlow nodes when blocks data changes."""
        if not blocks_data:
            return no_update
        return make_nodes(blocks_data)
    
    @app.callback(
        Output("blocks-store", "data", allow_duplicate=True),
        Input("log-tick", "n_intervals"),
        State("blocks-store", "data"),
        prevent_initial_call=True,
    )
    def sync_pipeline_status(n, blocks_data):
        """
        Periodically sync pipeline execution status to blocks-store.
        
        Reads PipelineState.current_block and completed results to update
        block statuses in the UI (RUNNING / SUCCESS) while the pipeline
        is executing in its background thread.
        """
        pipeline_state = get_pipeline_state()
        
        if not blocks_data:
            return no_update
        
        if not pipeline_state.is_running():
            # Pipeline not running — check if any block still shows RUNNING
            # and mark it as completed (pipeline just finished)
            any_running = any(
                blk.get("status") == "RUNNING" for blk in blocks_data
            )
            if not any_running:
                return no_update
        
        current_block = pipeline_state.get_current_block()
        
        # Get the set of completed block IDs (thread-safe access)
        with pipeline_state.lock:
            completed_block_ids = set(pipeline_state.results.keys())
        
        updated = False
        new_blocks = copy.deepcopy(blocks_data)
        
        for blk in new_blocks:
            bid = blk.get("id")
            old_status = blk.get("status", "READY")
            
            if bid == current_block:
                # This block is currently executing
                if old_status != "RUNNING":
                    blk["status"] = "RUNNING"
                    updated = True
            elif bid in completed_block_ids:
                # This block has a result — mark as SUCCESS
                # (unless user already set a different status like CONFIGURED)
                if old_status == "RUNNING":
                    result = pipeline_state.get_latest_result(bid)
                    if isinstance(result, dict) and result.get("success") is False:
                        blk["status"] = "ERROR"
                    else:
                        blk["status"] = "SUCCESS"
                    updated = True
            else:
                # If pipeline is running but this block was previously RUNNING
                # and is no longer the current block and has no result,
                # it might have been skipped or completed without storing
                if old_status == "RUNNING" and pipeline_state.is_running():
                    # Check if a later block is now running (meaning this one finished)
                    try:
                        if current_block:
                            bid_idx = PIPELINE_ORDER.index(bid) if bid in PIPELINE_ORDER else -1
                            cur_idx = PIPELINE_ORDER.index(current_block) if current_block in PIPELINE_ORDER else -1
                            if cur_idx > bid_idx >= 0:
                                blk["status"] = "SUCCESS"
                                updated = True
                    except ValueError:
                        pass
        
        # Also handle: pipeline stopped, clear any lingering RUNNING status
        if not pipeline_state.is_running():
            for blk in new_blocks:
                if blk.get("status") == "RUNNING":
                    bid = blk.get("id")
                    if bid in completed_block_ids:
                        result = pipeline_state.get_latest_result(bid)
                        if isinstance(result, dict) and result.get("success") is False:
                            blk["status"] = "ERROR"
                        else:
                            blk["status"] = "SUCCESS"
                    else:
                        blk["status"] = "READY"
                    updated = True
        
        if not updated:
            return no_update
        
        return new_blocks
    
    @app.callback(
        Output("logs-output", "children"),
        Input("log-tick", "n_intervals"),
    )
    def update_logs(n):
        """
        Update the logs display.
        
        Shows logs from PipelineState (pipeline execution logs).
        UI logs are now written directly to PipelineState via the service layer.
        """
        pipeline_state = get_pipeline_state()
        logs = pipeline_state.get_logs()
        
        if not logs:
            return "No logs yet."
        
        return "\n".join(logs[-50:])
    
    @app.callback(
        Output("wait-modal", "is_open"),
        Output("wait-modal-message", "children"),
        Input("log-tick", "n_intervals"),
        State("wait-modal", "is_open"),
    )
    def update_wait_modal(n, is_open):
        """Update wait modal based on log state."""
        new_state = log_manager.sync_wait_state()
        
        if new_state is not None:
            return new_state["active"], new_state["message"]
        
        return no_update, no_update
