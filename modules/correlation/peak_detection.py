"""
Peak detection algorithms for correlation analysis.

Includes first peak detection for BOC signals (Line of Sight detection).
"""
from __future__ import annotations

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False


def _check_numpy() -> None:
    """Verify numpy is available."""
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for signal correlation but is not installed")


def find_first_peak(power: np.ndarray, threshold_ratio: float = 0.7) -> int:
    """
    Find the first significant peak in correlation power.
    
    For BOC signals (like E1B), the autocorrelation has multiple peaks.
    The first peak corresponds to the Line of Sight (LOS) signal.
    
    Algorithm:
    1. Find the maximum peak
    2. Define threshold as threshold_ratio * max_power
    3. Scan from the beginning to find the first sample above threshold
    4. Then find the local maximum around that point
    
    Args:
        power: Correlation power array
        threshold_ratio: Ratio of max power to use as threshold (default 0.7 = 70%)
    
    Returns:
        Index of the first significant peak
    """
    _check_numpy()
    
    if power.size == 0:
        return 0
    
    max_power = np.max(power)
    max_idx = int(np.argmax(power))
    
    if max_power <= 0:
        return max_idx
    
    threshold = threshold_ratio * max_power
    
    # Find first sample above threshold
    above_threshold = np.where(power >= threshold)[0]
    
    if len(above_threshold) == 0:
        return max_idx
    
    first_above = above_threshold[0]
    
    # Find local maximum around the first crossing
    # Search in a window around the first crossing point
    window_size = min(100, len(power) // 10)  # Adaptive window
    search_start = max(0, first_above - window_size // 2)
    search_end = min(len(power), first_above + window_size)
    
    local_max_idx = search_start + int(np.argmax(power[search_start:search_end]))
    
    return local_max_idx
