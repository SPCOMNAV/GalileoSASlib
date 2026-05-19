"""
Signal and chip loading functions for Galileo E1/E6 correlation.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

try:
    from utils import structsGalileoSignals as _sgs
except ImportError:
    _sgs = None

from .constants import E1_CODE_LENGTH_CHIPS, E6_CODE_LENGTH_CHIPS


def _check_numpy() -> None:
    """Verify numpy is available."""
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for signal correlation but is not installed")


def load_samples_sc16_window(file: str, start_sample: int, num_samples: int):
    """
    Load IQ samples from SC16 format file.
    
    Args:
        file: Path to SC16 file
        start_sample: Starting sample index
        num_samples: Number of samples to read (-1 for all)
    
    Returns:
        Complex IQ samples as np.complex64 array
    """
    _check_numpy()
    
    itemsize = np.dtype(np.int16).itemsize
    with open(file, "rb") as fh:
        fh.seek(max(0, start_sample * 2 * itemsize))
        count = num_samples * 2 if num_samples != -1 else -1
        raw = np.fromfile(fh, dtype=np.int16, count=count)
    
    if raw.size < 2:
        return np.zeros(0, dtype=np.complex64)
    if raw.size % 2:
        raw = raw[:-1]
    
    iq = raw[0::2].astype(np.float32) + 1j * raw[1::2].astype(np.float32)
    return iq / 32768.0


def load_chips_for_band(prn: int, band: str) -> np.ndarray:
    """
    Load spreading code chips for a given PRN and band.
    
    This is a unified function that loads chips from the appropriate dictionary
    based on the band type (E1B, E1C, E6B, E6C).
    
    Args:
        prn: PRN/SVID number
        band: Band type string ("E1B", "E1C", "E6B", "E6C")
    
    Returns:
        Chips in polar format (+1/-1) as np.int8 array
    """
    _check_numpy()
    
    if _sgs is None:
        raise RuntimeError("Galileo code tables (structsGalileoSignals) are not available")
    
    # Map band to dictionary and code length
    band_config = {
        "E1B": ("PRN_CODE_HEX_E1B_DICT", E1_CODE_LENGTH_CHIPS),
        "E1C": ("PRN_CODE_HEX_E1C_DICT", E1_CODE_LENGTH_CHIPS),
        "E6B": ("PRN_CODE_HEX_E6B_DICT", E6_CODE_LENGTH_CHIPS),
        "E6C": ("PRN_CODE_HEX_E6C_DICT", E6_CODE_LENGTH_CHIPS),
    }
    
    if band not in band_config:
        raise ValueError(f"Unknown band: {band}. Expected one of: {list(band_config.keys())}")
    
    dict_name, code_length = band_config[band]
    
    try:
        hex_map = getattr(_sgs, dict_name)
        hex_string = hex_map[int(prn)]
        chips = _sgs.hex_to_primary_code(hex_string, code_length)
    except (AttributeError, KeyError, ValueError) as exc:
        raise RuntimeError(f"{band} code not found for PRN {prn}") from exc
    
    return (1 - 2 * chips).astype(np.int8)


def load_e6b_chips(prn: int) -> np.ndarray:
    """Load E6B spreading code chips for given PRN."""
    return load_chips_for_band(prn, "E6B")


def load_e6c_chips(prn: int) -> np.ndarray:
    """Load E6C spreading code chips (unencrypted) for given PRN."""
    return load_chips_for_band(prn, "E6C")


def load_e1b_chips(prn: int) -> np.ndarray:
    """Load E1B spreading code chips for given PRN."""
    return load_chips_for_band(prn, "E1B")


def load_e1c_chips(prn: int) -> np.ndarray:
    """Load E1C spreading code chips for given PRN."""
    return load_chips_for_band(prn, "E1C")


def load_ecs_from_hex(ecs_hex: str) -> np.ndarray:
    """
    Convert ECS hex string to polar chips array.
    ECS is the Encryption Code Sequence from decrypted RECS.
    When the E6C signal is encrypted, the transmitted signal IS the ECS.
    
    Args:
        ecs_hex: Hex string of ECS (from RECSDecryption)
    
    Returns:
        Polar chips array (+1/-1), full length from RECS
    """
    _check_numpy()
    
    buf = bytes.fromhex(ecs_hex)
    bits = np.unpackbits(np.frombuffer(buf, dtype=np.uint8), bitorder='big')
    return (1 - 2 * bits).astype(np.int8)


def load_ecs_from_results() -> Optional[Tuple[str, str]]:
    """
    Load the most recent ECS from results folder.
    
    Returns:
        Tuple (ecs_hex, ecs_file_path) or None if not found
    """
    from utils.utils import get_results_subdir
    
    ecs_dir = get_results_subdir("ECS")
    
    if not ecs_dir.exists():
        return None
    
    # Find most recent ECS file
    ecs_files = list(ecs_dir.glob("*.bin"))
    if not ecs_files:
        return None
    
    # Sort by modification time, most recent first
    ecs_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    latest_ecs = ecs_files[0]
    
    # Read ECS hex from file
    ecs_bytes = latest_ecs.read_bytes()
    ecs_hex = ecs_bytes.hex().upper()
    
    return (ecs_hex, str(latest_ecs))
