"""
executor.py
-----------
Schema-driven pipeline executor.

Each block declares in its schema:
    consumes:  list[str]   keys read from the shared context
    produces:  list[str]   keys written to the shared context
    artifacts: dict        generated files (auto_open flag etc.)
    execution: dict        parallel_with, required_when, ...

Every block must expose a v1 run_pipeline:

    def run_pipeline(config: dict, inputs: dict, logger=print) -> dict:
        # config   = the block's own config_json/<Name>.json
        # inputs   = {key: ctx[key] for key in schema.consumes}
        # returns:
        #   {
        #     "outputs":   {key: value for key in schema.produces},   # required
        #     "artifacts": {name: path | {path, ...}},                # optional
        #     "summary":   {...},                                      # optional, UI/logs
        #     "data":      {...},                                      # optional, UI panel
        #   }
    run_pipeline.__contract_version__ = "1.0"
"""

from __future__ import annotations

import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from utils.io_helpers import load_schema, load_config
from utils.paths import console_log


CONTRACT_VERSION = "1.0"


class ContractError(RuntimeError):
    """Raised when a block's runtime behaviour disagrees with its schema."""


def _call_run_pipeline(fn, config, inputs, globals_view, logger):
    """Invoke a 1.0 run_pipeline, gracefully accepting the older 3-arg signature.

    Blocks migrated to the (config, inputs, globals, logger) signature receive
    the globals dict; blocks that still expose (config, inputs, logger)
    are called without it. Detection is based on parameter introspection and
    cached per function for cheap repeat calls.
    """
    accepts = getattr(fn, "_accepts_globals", None)
    if accepts is None:
        import inspect
        try:
            params = inspect.signature(fn).parameters
            accepts = "globals" in params or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
        except (TypeError, ValueError):
            accepts = False
        try:
            fn._accepts_globals = accepts  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            pass

    if accepts:
        return fn(config=config, inputs=inputs, globals=globals_view, logger=logger)
    return fn(config=config, inputs=inputs, logger=logger)


# ─────────────────────────────────────────────────────────────────────────────
# Required-when DSL
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_required_when(
    expr: str,
    ctx: Dict[str, Any],
    globals_view: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Evaluate a ``required_when`` expression against the current context.

    Supported forms (intentionally tiny):
        ""                  → always required
        "ctx.<key>"         → truthy of ctx[<key>]
        "!ctx.<key>"        → falsy of ctx[<key>]
        "globals.<key>"     → truthy of globals[<key>]
        "!globals.<key>"    → falsy of globals[<key>]

    Anything more elaborate should live in the block itself, not in the schema.
    """
    if not expr:
        return True
    expr = expr.strip()
    negate = expr.startswith("!")
    if negate:
        expr = expr[1:].strip()

    if expr.startswith("ctx."):
        source = ctx
        key = expr[len("ctx."):]
    elif expr.startswith("globals."):
        source = globals_view or {}
        key = expr[len("globals."):]
    else:
        raise ValueError(
            f"required_when must be empty, 'ctx.<key>' or 'globals.<key>' "
            f"(optionally negated with '!'); got {expr!r}"
        )

    value = source.get(key)
    truthy = bool(value) and (str(value).strip() != "" if isinstance(value, str) else True)
    return (not truthy) if negate else truthy


# ─────────────────────────────────────────────────────────────────────────────
# Executor
# ─────────────────────────────────────────────────────────────────────────────

class Executor:
    """
    Schema-driven pipeline executor.

    Usage:
        from modules import MODULE_REGISTRY
        ex = Executor(MODULE_REGISTRY)
        ex.run_pipeline(["ConfigInit", "TimeReferenceSynchronizer", ...])
        # or
        ex.run_single("BGDandRECSdownloader")   # auto-bootstraps ConfigInit
    """

    # Name of the block whose outputs populate the read-only globals channel.
    GLOBALS_PRODUCER = "ConfigInit"

    def __init__(
        self,
        registry: Dict[str, Dict[str, Any]],
        ctx: Optional[Dict[str, Any]] = None,
        logger: Callable[[str], None] = print,
    ) -> None:
        self.registry = registry
        self.ctx: Dict[str, Any] = ctx if ctx is not None else {}
        # globals = system-wide config (published once by ConfigInit and then
        # available to every block without it having to declare a `consumes`).
        self.globals: Dict[str, Any] = {}
        self.logger = logger
        self._schemas: Dict[str, Dict[str, Any]] = {}
        self._configs: Dict[str, Dict[str, Any]] = {}
        self._bg_threads: List[threading.Thread] = []
        self._bg_errors: List[Exception] = []
        # Observer hooks — wrappers (e.g. PipelineRunner for the Dash UI) can
        # subscribe to lifecycle events without subclassing.
        self.on_module_start:    Optional[Callable[[str], None]] = None
        self.on_module_complete: Optional[Callable[[str, Dict[str, Any]], None]] = None
        self.on_module_failed:   Optional[Callable[[str, Exception], None]] = None
        # Cooperative-stop probe. The pipeline checks this between blocks; the
        # wrapper plugs in `lambda: state.is_running()` etc. Default = always go.
        self.should_continue:    Callable[[], bool] = lambda: True

    # ── Schema / config caching ─────────────────────────────────────────────
    def schema(self, name: str) -> Dict[str, Any]:
        if name not in self._schemas:
            self._schemas[name] = load_schema(name)
        return self._schemas[name]
    def config(self, name: str) -> Dict[str, Any]:
        if name not in self._configs:
            self._configs[name] = load_config(name)
        return self._configs[name]

    # ── Logger factory ──────────────────────────────────────────────────────
    def _make_logger(self, name: str) -> Callable[[str], None]:
        prefix = f"[{name}]"
        outer = self.logger
        def _log(msg: str) -> None:
            outer(f"{prefix} {msg}")
        return _log

    def _resolve_produces(self, name: str, schema: Dict[str, Any]) -> List[str]:
        """Resolve the list of context keys a block must publish.

        For the globals producer (ConfigInit) the list is auto-derived from
        ``sections[].fields[].id`` so the schema only declares it in one place.
        Other blocks declare ``produces`` explicitly because their outputs are
        derived runtime values (correlation_results, ecs_hex, ...) that have
        no UI representation.
        """
        if name == self.GLOBALS_PRODUCER:
            from utils.schema_validation import schema_field_ids
            return schema_field_ids(schema)
        return list(schema.get("produces", []))

    # ── Single block execution ──────────────────────────────────────────────
    def run_module(self, name: str) -> Dict[str, Any]:
        if name not in self.registry:
            raise KeyError(f"Module '{name}' not in registry")

        schema = self.schema(name)
        config = self.config(name)
        consumes: List[str] = list(schema.get("consumes", []))
        produces: List[str] = self._resolve_produces(name, schema)

        inputs = {k: self.ctx.get(k) for k in consumes}
        globals_view = dict(self.globals)
        run_pipeline = self.registry[name]["run_pipeline"]
        log = self._make_logger(name)

        if getattr(run_pipeline, "__contract_version__", None) != CONTRACT_VERSION:
            raise ContractError(
                f"{name}: run_pipeline must declare __contract_version__ = '{CONTRACT_VERSION}'."
            )

        if self.on_module_start:
            try:
                self.on_module_start(name)
            except Exception:
                traceback.print_exc()

        try:
            result = _call_run_pipeline(run_pipeline, config, inputs, globals_view, log)
            self._validate_outputs(name, produces, result)

            # ConfigInit's outputs feed the globals channel; everyone else feeds ctx.
            if name == self.GLOBALS_PRODUCER:
                outputs = result.get("outputs", {}) if isinstance(result, dict) else {}
                self.globals.update(outputs)
            else:
                self._store_outputs(name, produces, result)

            self._handle_artifacts(name, schema, result)
        except Exception as exc:
            if self.on_module_failed:
                try:
                    self.on_module_failed(name, exc)
                except Exception:
                    traceback.print_exc()
            raise

        if self.on_module_complete:
            try:
                self.on_module_complete(name, result)
            except Exception:
                traceback.print_exc()

        return result

    # ── Validation / publishing ─────────────────────────────────────────────
    def _validate_outputs(self, name: str, produces: List[str], result: Any) -> None:
        if not isinstance(result, dict):
            raise ContractError(f"{name}: run_pipeline must return a dict, got {type(result).__name__}")
        if "outputs" not in result:
            raise ContractError(f"{name}: result is missing required 'outputs' key")
        outputs = result["outputs"]
        if not isinstance(outputs, dict):
            raise ContractError(f"{name}: 'outputs' must be a dict, got {type(outputs).__name__}")
        declared = set(produces)
        produced = set(outputs.keys())
        if declared != produced:
            missing = declared - produced
            extra = produced - declared
            raise ContractError(
                f"{name}: outputs do not match schema.produces. "
                f"missing={sorted(missing)} extra={sorted(extra)}"
            )

    def _store_outputs(self, name: str, produces: List[str], result: Dict[str, Any]) -> None:
        outputs = result.get("outputs", {})
        for key in produces if produces else outputs.keys():
            if key in outputs:
                self.ctx[key] = outputs[key]
        # Legacy path may have published extra keys (e.g. recs_files alias)
        for key, value in outputs.items():
            if key not in self.ctx or self.ctx[key] is None:
                self.ctx[key] = value

    def _handle_artifacts(self, name: str, schema: Dict[str, Any], result: Dict[str, Any]) -> None:
        artifacts = result.get("artifacts") or {}
        declared = schema.get("artifacts", {}) or {}
        for art_name, art_value in artifacts.items():
            spec = declared.get(art_name) or {}
            path = art_value if isinstance(art_value, str) else art_value.get("path")
            if not path:
                continue
            p = Path(path)
            if spec.get("auto_open") and p.exists():
                try:
                    webbrowser.open(f"file://{p.resolve()}")
                except Exception as exc:
                    self.logger(f"[{name}] Warning: could not auto-open artifact {path}: {exc}")

    # ── Pipeline execution ──────────────────────────────────────────────────
    def run_pipeline(self, order: List[str]) -> Dict[str, Any]:
        """
        Execute the given ordered list of block names.

        Honours per-block schema.execution:
          - required_when: skip the block when the condition is falsy
          - parallel_with: launch the block in a background thread and only
            join it when a downstream block consumes one of its produces

        Returns the final shared context.
        """
        self._bg_threads.clear()
        self._bg_errors.clear()

        # Map of "produced_key -> background thread that will fill it"
        producers_in_flight: Dict[str, threading.Thread] = {}

        for name in order:
            if not self.should_continue():
                self.logger("Pipeline stop requested — exiting loop.")
                break

            schema = self.schema(name)
            execution = schema.get("execution", {}) or {}
            required_when = execution.get("required_when", "")

            if not evaluate_required_when(required_when, self.ctx, self.globals):
                self.logger(f"[{name}] Skipped (required_when={required_when!r} not satisfied)")
                continue

            # Before running this block, join any in-flight producer of a key
            # that this block consumes.
            for needed in schema.get("consumes", []):
                if needed in producers_in_flight:
                    th = producers_in_flight.pop(needed)
                    self.logger(f"[{name}] waiting on background producer of {needed!r}…")
                    th.join()

            parallel_with = execution.get("parallel_with") or []
            if parallel_with:
                # Run in the background while the named "anchor" blocks proceed.
                th = threading.Thread(
                    target=self._bg_run, args=(name,), daemon=True, name=f"{name}-bg"
                )
                th.start()
                for produced in schema.get("produces", []):
                    producers_in_flight[produced] = th
                self._bg_threads.append(th)
            else:
                try:
                    self.run_module(name)
                except Exception as exc:
                    self.logger(f"[{name}] FAILED: {exc}")
                    traceback.print_exc()
                    raise

        # Drain any remaining background threads (e.g. blocks whose producers
        # were never consumed in this run).
        for th in self._bg_threads:
            if th.is_alive():
                th.join(timeout=120)
        if self._bg_errors:
            raise self._bg_errors[0]

        return self.ctx

    def _bg_run(self, name: str) -> None:
        try:
            self.run_module(name)
        except Exception as exc:
            self._bg_errors.append(exc)
            console_log("Executor", f"Background error in {name}: {exc}")
            traceback.print_exc()

    # ── Single block execution (UI single-block button) ─────────────────────
    def run_single(self, name: str, bootstrap: bool = True) -> Dict[str, Any]:
        """Run a single block. Optionally bootstrap by running ConfigInit first
        so that the block's consumed config_init keys are present in ctx."""
        if bootstrap and name != "ConfigInit" and "ConfigInit" in self.registry:
            try:
                self.run_module("ConfigInit")
            except Exception as exc:
                self.logger(f"[bootstrap] ConfigInit failed: {exc}")
        return self.run_module(name)


__all__ = [
    "CONTRACT_VERSION",
    "ContractError",
    "Executor",
    "evaluate_required_when",
]
