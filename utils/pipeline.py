"""
pipeline.py
-----------
Pipeline service: combines an Executor (the schema-driven engine) with a
PipelineState (the UI-poll-friendly state holder) and a daemon thread so a UI
can drive the pipeline without blocking.

This module is the only place that knows how to:

  * spin up a thread for the pipeline
  * snapshot the execution config to ``executions/<id>/execution.json``
  * forward executor logs/events into the PipelineState
  * stop cooperatively when the UI clicks Stop

SASpipeline.py is a thin facade on top of this — it just exposes a singleton
Pipeline instance and a couple of helpers for the UI.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from utils.execution_context import ExecutionContext
from utils.executor import Executor
from utils.io_helpers import serialise_for_store
from utils.paths import clear_results_subdir, console_log
from utils.pipeline_configs import (
    apply_pipeline_config,
    load_pipeline_config,
    write_execution_config,
)


# ─────────────────────────────────────────────────────────────────────────────
# PipelineState — UI-pollable state holder
# ─────────────────────────────────────────────────────────────────────────────

class PipelineState:
    """Thread-safe holder for the data the UI sondeas: log queue, current
    block, latest result per block, and a running flag for cooperative stop."""

    def __init__(self, max_log_entries: int = 1000):
        self.logs: deque[str] = deque(maxlen=max_log_entries)
        self.running: bool = False
        self.lock = threading.RLock()
        self.results: Dict[str, Any] = {}
        self.current_block: Optional[str] = None

    def start(self) -> None:
        with self.lock:
            self.running = True
            self.current_block = None
            self.logs.clear()
            self.results.clear()
            self.append_log("Pipeline starting...")

    def stop(self) -> None:
        with self.lock:
            if self.running:
                self.running = False
                self.current_block = None
                self.append_log("Pipeline stopped.")

    def is_running(self) -> bool:
        with self.lock:
            return self.running

    def set_current_module(self, block_id: Optional[str]) -> None:
        with self.lock:
            self.current_block = block_id

    def get_current_module(self) -> Optional[str]:
        with self.lock:
            return self.current_block

    def append_log(self, message: str, print_to_console: bool = True) -> None:
        with self.lock:
            self.logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")
            if print_to_console:
                print(f"[Log] {message}")

    def append_log_raw(self, message: str) -> None:
        with self.lock:
            self.logs.append(f"[{time.strftime('%H:%M:%S')}] {message}")

    def get_logs(self) -> List[str]:
        with self.lock:
            return list(self.logs)

    def set_result(self, block_id: str, payload: Any) -> None:
        with self.lock:
            self.results[block_id] = serialise_for_store(payload)
            console_log("PIPELINESTATE", f"Result saved for block id: '{block_id}'.", to_ui=False)

    def get_latest_result(self, block_id: str) -> Optional[Any]:
        with self.lock:
            return deepcopy(self.results.get(block_id))


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline — the service object that owns Executor + thread + PipelineState
# ─────────────────────────────────────────────────────────────────────────────

class Pipeline:
    """Run a SAS-style pipeline either asynchronously (UI mode) or synchronously
    (script/CLI). Owns its Executor, optionally a daemon thread, and a
    PipelineState that the UI sondeas."""

    def __init__(
        self,
        registry: Dict[str, Dict[str, Any]],
        order: List[str],
        state: Optional[PipelineState] = None,
        config_init_module: str = "ConfigInit",
    ) -> None:
        self.registry = registry
        self.order = list(order)
        self.state = state if state is not None else PipelineState()
        self._config_init = config_init_module
        self._thread: Optional[threading.Thread] = None
        self._execution_id: Optional[str] = None
        self._ctx_singleton = ExecutionContext()  # only for execution_id + path helpers

        self.executor = Executor(registry, logger=self._ui_logger)
        self.executor.on_module_start    = self._on_module_start
        self.executor.on_module_complete = self._on_module_complete
        self.executor.on_module_failed   = self._on_module_failed
        self.executor.should_continue    = lambda: self.state.is_running()

    # ── UI bridge ───────────────────────────────────────────────────────────
    def _ui_logger(self, msg: str) -> None:
        self.state.append_log_raw(msg)

    def _display_name(self, name: str) -> str:
        return self.registry.get(name, {}).get("display_name", name)

    def _on_module_start(self, name: str) -> None:
        self.state.set_current_module(name)
        self.state.append_log(f"Executing: {self._display_name(name)}")

    def _on_module_complete(self, name: str, result: Any) -> None:
        self.state.set_result(name, serialise_for_store(result))
        self.state.append_log(f"Completed: {self._display_name(name)}")
        self.state.set_current_module(None)

    def _on_module_failed(self, name: str, exc: Exception) -> None:
        self.state.append_log(f"FAILED: {self._display_name(name)} - {exc}")
        self.state.set_current_module(None)
        console_log("Pipeline", f"Exception in block '{name}': {exc}")

    # ── Public API ──────────────────────────────────────────────────────────
    @property
    def thread(self) -> Optional[threading.Thread]:
        return self._thread

    def run_async(self) -> None:
        """Spawn a daemon thread that runs the full pipeline."""
        if self.state.is_running():
            console_log("Pipeline", "Already running; start request ignored.", to_ui=False)
            return
        self._execution_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.state.start()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def run_sync(self) -> PipelineState:
        """Run the full pipeline synchronously in the current thread."""
        self._execution_id = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
        self.state.start()
        self._worker()
        return self.state

    def stop(self, join_timeout: float = 2.0) -> None:
        self.state.stop()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=join_timeout)

    def run_block(
        self,
        name: str,
        params: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Run a single block via Executor.run_single (auto-bootstrapping
        ConfigInit when needed). Optional ``params``/``context`` are merged on
        top of the persisted config."""
        if not self._ctx_singleton.get_execution_id():
            self._ctx_singleton.set_execution_id(
                datetime.now().strftime("%Y%m%d_%H%M%S")
            )

        if params or context:
            from utils.io_helpers import load_config as _load_config
            merged = deepcopy(_load_config(name)) if name in self.registry else {}
            if params:
                merged.update(params)
            if context:
                merged.update(context)
            self.executor._configs[name] = merged

        return self.executor.run_single(
            name, bootstrap=(name != self._config_init),
        )

    def run_from_config(
        self,
        config_path: str,
        sync: bool = False,
    ) -> Optional[PipelineState]:
        """Apply a saved pipeline config and run. If ``sync`` is True the call
        blocks and the final ``PipelineState`` is returned; otherwise a thread
        is spawned and the call returns immediately."""
        config = load_pipeline_config(config_path)
        result = apply_pipeline_config(config)
        metadata = config.get("metadata", {})
        config_name = metadata.get("execution_name") or metadata.get("execution_id") or "unnamed"
        console_log("Pipeline", f"Loaded config '{config_name}' "
                                f"({len(result['applied_modules'])} blocks)")
        if result.get("errors"):
            console_log("Pipeline", f"Warning: some blocks had errors: {result['errors']}")
        if sync:
            return self.run_sync()
        self.run_async()
        return None

    # ── Worker thread ───────────────────────────────────────────────────────
    def _worker(self) -> None:
        """Main loop: reset state, snapshot config, delegate to executor."""
        self.executor.ctx.clear()
        self.executor.globals.clear()
        self._ctx_singleton.clear()
        self._ctx_singleton.set_execution_id(self._execution_id)

        deleted = clear_results_subdir("ECS")
        if deleted > 0:
            console_log("Pipeline", f"Cleared {deleted} old file(s) from ECS folder")

        try:
            self._save_execution_config()
            self.executor.run_pipeline(self.order)
            self.state.append_log("Pipeline completed.")
        except Exception:
            self.state.append_log("Pipeline terminated due to unexpected error.")
            traceback.print_exc()
        finally:
            self.state.set_current_module(None)
            self.state.stop()

    def _save_execution_config(self) -> None:
        """Snapshot the full config to ``executions/<id>/execution.json`` and
        (if save_config_on_run) to ``pipeline_configs/``."""
        try:
            from utils.io_helpers import load_config as _load_config
            cfg_init = _load_config(self._config_init)
            written = write_execution_config(
                execution_id=self._execution_id or "",
                pipeline_order=self.order,
                execution_name=cfg_init.get("execution_name", "").strip(),
                output_dir=self._ctx_singleton.get_execution_results_dir(),
                also_save_to_pipeline_configs=bool(cfg_init.get("save_config_on_run", True)),
                config_output_dir=cfg_init.get("config_output_dir", "pipeline_configs"),
            )
            for label, path in written.items():
                self.state.append_log(f"{label.capitalize()} config saved to: {path}")
        except Exception as e:
            self.state.append_log(f"Warning: Could not save configuration: {e}")


__all__ = ["Pipeline", "PipelineState"]
