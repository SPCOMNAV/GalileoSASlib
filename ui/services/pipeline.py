from typing import List, Dict, Any, Tuple, Optional

from ui.core.types import PipelineResult
from ui.core.state import log_manager

# Import from SASpipeline (external dependency)
from SASpipeline import (
    load_modules,
    get_pipeline_state,
    start_pipeline as _start_pipeline,
    stop_pipeline as _stop_pipeline,
    run_single_module as _run_single_module,
    get_module_run_pipeline,
    get_module_schema,
    get_module_default_config,
    # Config management functions
    list_saved_configs as _list_saved_configs,
    load_pipeline_config as _load_pipeline_config,
    apply_pipeline_config as _apply_pipeline_config,
    save_current_config as _save_current_config,
    run_from_config as _run_from_config,
    # Module name constants
    MODULE_CONFIG_INIT,
    MODULE_TIME_SYNC,
    MODULE_ALMANAC,
    MODULE_DOWNLOADER,
    MODULE_PARSER,
    MODULE_UNCERTAINTY,
    MODULE_SNAPSHOT,
    MODULE_TESLA,
    MODULE_RECS,
    MODULE_SIGNAL_CORR,
    MODULE_EPHEMERIDES,
    MODULE_AUTHENTICATOR,
    MODULE_PVT,
    PIPELINE_ORDER,
    INDEPENDENT_MODULES,
)


class PipelineService:
    """
    Clean interface for interacting with the pipeline.

    Wraps SASpipeline functions with consistent return types and logging.
    """

    # Expose module constants
    MODULE_CONFIG_INIT = MODULE_CONFIG_INIT
    MODULE_TIME_SYNC = MODULE_TIME_SYNC
    MODULE_DOWNLOADER = MODULE_DOWNLOADER
    MODULE_PARSER = MODULE_PARSER
    MODULE_UNCERTAINTY = MODULE_UNCERTAINTY
    MODULE_SNAPSHOT = MODULE_SNAPSHOT
    MODULE_TESLA = MODULE_TESLA
    MODULE_RECS = MODULE_RECS
    MODULE_SIGNAL_CORR = MODULE_SIGNAL_CORR
    MODULE_EPHEMERIDES = MODULE_EPHEMERIDES
    MODULE_AUTHENTICATOR = MODULE_AUTHENTICATOR
    MODULE_ALMANAC = MODULE_ALMANAC
    MODULE_PVT = MODULE_PVT
    PIPELINE_ORDER = PIPELINE_ORDER
    INDEPENDENT_MODULES = INDEPENDENT_MODULES

    def load_modules(self) -> List[Dict[str, Any]]:
        """Load initial module configurations."""
        return load_modules()

    def get_state(self) -> Any:
        """Get current pipeline state."""
        return get_pipeline_state()

    def start(self) -> PipelineResult:
        """Start the pipeline."""
        try:
            _start_pipeline()
            log_manager.append("Pipeline started.")
            return {
                "success": True,
                "message": "Pipeline started.",
                "data": None,
                "error": None,
            }
        except Exception as e:
            log_manager.append(f"Error starting pipeline: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def stop(self) -> PipelineResult:
        """Stop the running pipeline."""
        try:
            _stop_pipeline()
            log_manager.append("Pipeline stop requested.")
            return {
                "success": True,
                "message": "Pipeline stop requested.",
                "data": None,
                "error": None,
            }
        except Exception as e:
            log_manager.append(f"Error stopping pipeline: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def run_module(self, block_id: str, params: Dict[str, Any]) -> PipelineResult:
        """Run a single module with given parameters."""
        try:
            success, result = _run_single_module(block_id, params)

            if success:
                log_manager.append(f"Module {block_id} executed successfully.")
                return {
                    "success": True,
                    "message": f"Module {block_id} completed.",
                    "data": result if isinstance(result, dict) else None,
                    "error": None,
                }
            else:
                error_msg = str(result) if result else "Unknown error"
                log_manager.append(f"Module {block_id} failed: {error_msg}")
                return {
                    "success": False,
                    "message": error_msg,
                    "data": None,
                    "error": error_msg,
                }
        except Exception as e:
            log_manager.append(f"Error running module {block_id}: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def get_module_schema(self, block_id: str) -> Optional[Dict[str, Any]]:
        """Get the schema for a module."""
        return get_module_schema(block_id)

    def get_module_default_config(self, block_id: str) -> Dict[str, Any]:
        """Get default configuration for a module."""
        return get_module_default_config(block_id)

    def get_module_run_pipeline(self, block_id: str) -> Optional[callable]:
        """Get the run_pipeline function for a module."""
        return get_module_run_pipeline(block_id)

    # =========== Config Management Methods ===========

    def list_configs(self) -> PipelineResult:
        """List all saved pipeline configurations."""
        try:
            configs = _list_saved_configs()
            return {
                "success": True,
                "message": f"Found {len(configs)} saved configuration(s).",
                "data": configs,
                "error": None,
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def load_config(self, config_path: str) -> PipelineResult:
        """
        Load a saved pipeline configuration.

        Args:
            config_path: Path to config file (absolute or relative to pipeline_configs/)

        Returns:
            PipelineResult with the loaded config data
        """
        try:
            config = _load_pipeline_config(config_path)
            metadata = config.get("metadata", {})
            name = metadata.get("execution_name") or metadata.get("execution_id") or "unnamed"
            log_manager.append(f"Loaded config: '{name}'")
            return {
                "success": True,
                "message": f"Configuration '{name}' loaded.",
                "data": config,
                "error": None,
            }
        except FileNotFoundError as e:
            log_manager.append(f"Config not found: {config_path}")
            return {
                "success": False,
                "message": f"Configuration not found: {config_path}",
                "data": None,
                "error": str(e),
            }
        except Exception as e:
            log_manager.append(f"Error loading config: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def apply_config(self, config: Dict[str, Any]) -> PipelineResult:
        """
        Apply a loaded configuration to the config_json/ files.

        Args:
            config: Config dict with "metadata" and "module_configs" keys

        Returns:
            PipelineResult with list of applied modules
        """
        try:
            result = _apply_pipeline_config(config)

            if result["status"] == "success":
                log_manager.append(
                    f"Applied config to {len(result['applied_modules'])} modules."
                )
                return {
                    "success": True,
                    "message": f"Configuration applied to {len(result['applied_modules'])} modules.",
                    "data": result,
                    "error": None,
                }
            else:
                log_manager.append(
                    f"Config partially applied. Errors: {result['errors']}"
                )
                return {
                    "success": True,  # Partial success
                    "message": f"Configuration partially applied. {len(result['errors'])} error(s).",
                    "data": result,
                    "error": None,
                }
        except Exception as e:
            log_manager.append(f"Error applying config: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def save_config(self, name: Optional[str] = None) -> PipelineResult:
        """
        Save the current pipeline configuration.

        Args:
            name: Optional name for the config (uses timestamp if None)

        Returns:
            PipelineResult with path to saved config
        """
        try:
            path = _save_current_config(name)
            display_name = name or "current config"
            log_manager.append(f"Saved '{display_name}' to: {path}")
            return {
                "success": True,
                "message": f"Configuration saved to {path}",
                "data": {"path": path},
                "error": None,
            }
        except Exception as e:
            log_manager.append(f"Error saving config: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }

    def start_from_config(self, config_path: str) -> PipelineResult:
        """
        Load a saved config and start the pipeline.

        Combines load_config(), apply_config(), and start() in one call.

        Args:
            config_path: Path to config file

        Returns:
            PipelineResult
        """
        try:
            _run_from_config(config_path)
            log_manager.append(f"Pipeline started from: {config_path}")
            return {
                "success": True,
                "message": f"Pipeline started from {config_path}",
                "data": None,
                "error": None,
            }
        except FileNotFoundError:
            log_manager.append(f"Config not found: {config_path}")
            return {
                "success": False,
                "message": f"Configuration not found: {config_path}",
                "data": None,
                "error": f"File not found: {config_path}",
            }
        except Exception as e:
            log_manager.append(f"Error starting from config: {e}")
            return {
                "success": False,
                "message": str(e),
                "data": None,
                "error": str(e),
            }


# Singleton instance
pipeline_service = PipelineService()
