"""
modules package
---------------
Unified pipeline modules that combine pipeline + controller.

Each module has:
- default_json/<module>_schema.json: Schema with metadata + default values
- config_json/<module>.json: Current configuration values
- modules/Module_<name>.py: Unified pipeline + controller logic

To create a new module:
1. Create the schema JSON in default_json/<name>_schema.json
2. Create the file modules/Module_<name>.py following Module_RECSDecryption.py pattern
3. The UI is generated automatically from ui_module_popups/ui_generic.py
"""

from pathlib import Path

MODULES_DIR = Path(__file__).resolve().parent
BASE_DIR = MODULES_DIR.parent

# Registry of available modules
MODULE_REGISTRY = {}


def register_module(module_name, module_obj):
    """Register a module in the registry."""
    MODULE_REGISTRY[module_name] = module_obj


def get_module(module_name):
    """Get a module from the registry."""
    return MODULE_REGISTRY.get(module_name)


def discover_modules():
    """Discover all available modules in the directory."""
    modules = []
    for path in MODULES_DIR.glob("Module_*.py"):
        module_name = path.stem.replace("Module_", "")
        modules.append(module_name)
    return sorted(modules)


# Auto-import available modules

# RECSDecryption
try:
    from .Module_RECSDecryption import (
        run_pipeline as recs_run_pipeline,
        load_schema as recs_load_schema,
        load_config as recs_load_config,
    )
    register_module("RECSDecryption", {
        "run_pipeline": recs_run_pipeline,
        "load_schema": recs_load_schema,
        "load_config": recs_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import RECSDecryption: {e}")

# BGDandRECSdownloader
try:
    from .Module_BGDandRECSdownloader import (
        run_pipeline as downloader_run_pipeline,
        load_schema as downloader_load_schema,
        load_config as downloader_load_config,
    )
    register_module("BGDandRECSdownloader", {
        "run_pipeline": downloader_run_pipeline,
        "load_schema": downloader_load_schema,
        "load_config": downloader_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import BGDandRECSdownloader: {e}")

# BGDandRECSparser
try:
    from .Module_BGDandRECSparser import (
        run_pipeline as parser_run_pipeline,
        load_schema as parser_load_schema,
        load_config as parser_load_config,
    )
    register_module("BGDandRECSparser", {
        "run_pipeline": parser_run_pipeline,
        "load_schema": parser_load_schema,
        "load_config": parser_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import BGDandRECSparser: {e}")

# UncertaintyModule
try:
    from .Module_UncertaintyModule import (
        run_pipeline as uncertainty_run_pipeline,
        load_schema as uncertainty_load_schema,
        load_config as uncertainty_load_config,
    )
    register_module("UncertaintyModule", {
        "run_pipeline": uncertainty_run_pipeline,
        "load_schema": uncertainty_load_schema,
        "load_config": uncertainty_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import UncertaintyModule: {e}")

# SnapshotRecording
try:
    from .Module_SnapshotRecording import (
        run_pipeline as snapshot_run_pipeline,
        load_schema as snapshot_load_schema,
        load_config as snapshot_load_config,
    )
    register_module("SnapshotRecording", {
        "run_pipeline": snapshot_run_pipeline,
        "load_schema": snapshot_load_schema,
        "load_config": snapshot_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import SnapshotRecording: {e}")

# TESLAkeyManagement
try:
    from .Module_TESLAkeyManagement import (
        run_pipeline as tesla_run_pipeline,
        load_schema as tesla_load_schema,
        load_config as tesla_load_config,
    )
    register_module("TESLAkeyManagement", {
        "run_pipeline": tesla_run_pipeline,
        "load_schema": tesla_load_schema,
        "load_config": tesla_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import TESLAkeyManagement: {e}")

# SignalCorrelation
try:
    from .Module_SignalCorrelation import (
        run_pipeline as correlation_run_pipeline,
        load_schema as correlation_load_schema,
        load_config as correlation_load_config,
    )
    register_module("SignalCorrelation", {
        "run_pipeline": correlation_run_pipeline,
        "load_schema": correlation_load_schema,
        "load_config": correlation_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import SignalCorrelation: {e}")

# ConfigInit
try:
    from .Module_ConfigInit import (
        run_pipeline as configinit_run_pipeline,
        load_schema as configinit_load_schema,
        load_config as configinit_load_config,
    )
    register_module("ConfigInit", {
        "run_pipeline": configinit_run_pipeline,
        "load_schema": configinit_load_schema,
        "load_config": configinit_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import ConfigInit: {e}")

# TimeReferenceSynchronizer
try:
    from .Module_TimeReferenceSynchronizer import (
        run_pipeline as timesync_run_pipeline,
        load_schema as timesync_load_schema,
        load_config as timesync_load_config,
    )
    register_module("TimeReferenceSynchronizer", {
        "run_pipeline": timesync_run_pipeline,
        "load_schema": timesync_load_schema,
        "load_config": timesync_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import TimeReferenceSynchronizer: {e}")

# SatelliteAuthenticator
try:
    from .Module_SatelliteAuthenticator import (
        run_pipeline as authenticator_run_pipeline,
        load_schema as authenticator_load_schema,
        load_config as authenticator_load_config,
    )
    register_module("SatelliteAuthenticator", {
        "run_pipeline": authenticator_run_pipeline,
        "load_schema": authenticator_load_schema,
        "load_config": authenticator_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import SatelliteAuthenticator: {e}")

# AlmanacManagement (after TimeReferenceSynchronizer in pipeline)
try:
    from .Module_AlmanacManagement import (
        run_pipeline as almanac_run_pipeline,
        load_schema as almanac_load_schema,
        load_config as almanac_load_config,
    )
    register_module("AlmanacManagement", {
        "run_pipeline": almanac_run_pipeline,
        "load_schema": almanac_load_schema,
        "load_config": almanac_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import AlmanacManagement: {e}")

# EphemeridesManagement (between SignalCorrelation and SatelliteAuthenticator)
try:
    from .Module_EphemeridesManagement import (
        run_pipeline as ephemerides_run_pipeline,
        load_schema as ephemerides_load_schema,
        load_config as ephemerides_load_config,
    )
    register_module("EphemeridesManagement", {
        "run_pipeline": ephemerides_run_pipeline,
        "load_schema": ephemerides_load_schema,
        "load_config": ephemerides_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import EphemeridesManagement: {e}")

# PVTComputation (final module in pipeline)
try:
    from .Module_PVTComputation import (
        run_pipeline as pvt_run_pipeline,
        load_schema as pvt_load_schema,
        load_config as pvt_load_config,
    )
    register_module("PVTComputation", {
        "run_pipeline": pvt_run_pipeline,
        "load_schema": pvt_load_schema,
        "load_config": pvt_load_config,
    })
except ImportError as e:
    print(f"[modules] Failed to import PVTComputation: {e}")


__all__ = [
    "MODULES_DIR",
    "BASE_DIR",
    "MODULE_REGISTRY",
    "register_module",
    "get_module",
    "discover_modules",
]
