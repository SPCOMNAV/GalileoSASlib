"""
Metrics calculation for correlation analysis.

Includes PPSP, SNR, C/N0 estimation, and peak interpolation.
"""
from __future__ import annotations

import math
from typing import Tuple

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


def calculate_ppsp_db(
    correlation_power,
    peak_index: int,
    exclusion_width: int,
    samples_per_code: int = None,
    eps: float = 1e-12,
) -> float:
    """
    Calculate Primary-to-Secondary Peak Ratio in dB.
    
    Args:
        correlation_power: Full correlation power array
        peak_index: Index of the primary peak
        exclusion_width: Samples to exclude around the primary peak
        samples_per_code: Samples per code period. If provided, PPSP is calculated
                         only within ONE code period centered on the peak.
                         This avoids multiple peaks from repeating codes affecting the metric.
        eps: Small value to avoid division by zero
    
    Returns:
        PPSP in dB (primary_peak / secondary_peak)
    """
    _check_numpy()
    
    n = int(correlation_power.size)
    if n == 0 or peak_index < 0 or peak_index >= n:
        return float("-inf")

    primary_peak_value = float(correlation_power[int(peak_index)])
    if not math.isfinite(primary_peak_value) or primary_peak_value <= eps:
        return float("-inf")

    # Define search region: either one code period or full array
    if samples_per_code is not None and samples_per_code > 0:
        # Search only within one code period centered on the peak
        half_period = samples_per_code // 2
        region_start = max(0, peak_index - half_period)
        region_end = min(n, peak_index + half_period)
    else:
        # Search in full array (old behavior)
        region_start = 0
        region_end = n
    
    # Exclusion zone around the peak
    excl_start = max(region_start, int(peak_index) - int(exclusion_width))
    excl_end = min(region_end, int(peak_index) + int(exclusion_width) + 1)

    left_max = -float("inf")
    right_max = -float("inf")
    
    # Search left of exclusion zone (but within region)
    if excl_start > region_start:
        left_seg = correlation_power[region_start:excl_start]
        if left_seg.size:
            left_max = float(np.max(left_seg))
    
    # Search right of exclusion zone (but within region)
    if excl_end < region_end:
        right_seg = correlation_power[excl_end:region_end]
        if right_seg.size:
            right_max = float(np.max(right_seg))

    secondary_peak_value = max(left_max, right_max)

    if not math.isfinite(secondary_peak_value) or secondary_peak_value <= eps:
        return float("inf")

    return 10.0 * math.log10(primary_peak_value / secondary_peak_value)


def calculate_snr_peak_over_median_excl(power, peak_idx, exclusion_width=1) -> float:
    """
    Calculate SNR as peak over median, excluding region around peak.
    
    Args:
        power: Correlation power array
        peak_idx: Index of the peak
        exclusion_width: Half-width of exclusion zone
    
    Returns:
        SNR in dB
    """
    _check_numpy()
    
    n = int(power.size)
    if n == 0 or peak_idx < 0 or peak_idx >= n:
        return float("-inf")
    
    start_excl = max(0, int(peak_idx) - int(exclusion_width))
    end_excl = min(n, int(peak_idx) + int(exclusion_width) + 1)
    mask = np.ones(n, dtype=bool)
    mask[start_excl:end_excl] = False
    noise_median = np.median(power[mask]) if np.any(mask) else 1e-12
    
    primary_peak_value = float(power[int(peak_idx)])
    if not math.isfinite(primary_peak_value) or primary_peak_value <= 1e-12:
        return float("-inf")
    
    return 10.0 * math.log10(primary_peak_value / (noise_median + 1e-12))


def estimate_cn0_gonzalo_salcedo(
    signal_rx: np.ndarray,
    caf_peak_complex: complex,
    nscode: int,
    nc: int,
    ni: int,
    fs_hz: float,
    t_int_ref_ms: float = None
) -> float:
    """
    C/N0 estimation using Gonzalo-Salcedo method (Seco-Granados 2012).
    
    This non-coherent post-correlation estimator is designed for short acquisition
    windows where traditional SNR estimation is unreliable.
    
    Reference:
        G. López-Risueño, G. Seco-Granados, and A. García, "Evaluation of GPS 
        indoor positioning using real measurements and one-shot software receiver,"
        Proc. European Navigation Conf. GNSS, July 2005.
        
    Implementation based on: Rafael Terris Gallego, "C/N0 estimation", v0.2
    
    The key insight is that the estimator uses:
    - Pin: input signal power (from raw IQ samples)
    - CAF peak: output of matched filter (correlation)
    
    The relationship C/N0 = SNR_out * Bn where Bn = 1/Tint for coherent integration.
    
    Args:
        signal_rx: Received IQ signal samples (complex)
        caf_peak_complex: Complex value of CAF at peak (max of coherent CAF)
        nscode: Number of samples per code period [samples]
        nc: Number of coherent integrations
        ni: Number of non-coherent integrations  
        fs_hz: Sampling frequency [Hz]
        t_int_ref_ms: Reference integration time in ms for normalization.
                      If provided, normalizes C/N0 to this reference Tint.
                      Used for E6C (ECS) to normalize to 1ms like E6B.
        
    Returns:
        CN0 estimate in dB-Hz
    """
    _check_numpy()
    
    # Gonzalo-Salcedo estimator (Terris, v0.2, docs/estimate_cn0.pdf):
    #
    #   Bn  = Fs
    #   EZ  = |peakMaxVal|^2 * Nscode
    #   RNC = EZ * Nscode / Ni
    #   Pin = mean(|signalRx|^2)
    #   CN0 = (RNC * Bn - Nc * Nscode * Pin * Fs) / ((Nc * Nscode)^2 * Pin - RNC)
    #
    # IMPORTANT: Terris's compute_correlation applies a 1/NscodeEq
    # normalization so that |peakMaxVal| ~ A (signal amplitude).  Our
    # compute_correlation skips that normalization to preserve absolute
    # peak power for PPSP/SNR metrics, so we re-apply it here before
    # using the peak in this formula.

    pin = float(np.mean(np.abs(signal_rx) ** 2))
    if pin <= 0:
        return float('-inf')

    bn = fs_hz

    # Re-apply Terris's 1/Nscode normalization to the peak
    peak_norm = caf_peak_complex / nscode if nscode > 0 else caf_peak_complex
    peak_abs2 = float(np.abs(peak_norm) ** 2)
    ez = peak_abs2 * nscode
    rnc = ez * nscode / ni if ni > 0 else ez * nscode

    numerator = rnc * bn - nc * nscode * pin * fs_hz
    denominator = (nc * nscode) ** 2 * pin - rnc

    if denominator == 0 or numerator / denominator <= 0:
        return float('-inf')

    cn0_est = numerator / denominator
    cn0_db = 10.0 * math.log10(cn0_est)
    
    return cn0_db


def interpolate_caf_peak_1d(caf_1d, mode: str = "none", peak_idx: int = None) -> Tuple[float, float]:
    """
    Interpolate correlation peak for sub-sample accuracy.
    
    Args:
        caf_1d: 1D correlation power array
        mode: Interpolation mode:
              - "none": No interpolation
              - "piecewise-linear": Linear interpolation
              - "quadratic-polynomial-3-points": 3-point quadratic fit
              - "quadratic-polynomial-5-points": 5-point quadratic fit
        peak_idx: Optional pre-computed peak index (if None, uses argmax)
    
    Returns:
        Tuple (interpolated_peak_index, interpolated_peak_value)
    """
    _check_numpy()
    
    if caf_1d.size == 0 or mode == "none":
        if caf_1d.size == 0:
            return 0.0, 0.0
        if peak_idx is None:
            peak_idx = int(np.argmax(caf_1d))
        return float(peak_idx), float(caf_1d[peak_idx])

    mode = str(mode).lower()
    if peak_idx is None:
        peak_idx = int(np.argmax(caf_1d))
    peak_max_val = float(caf_1d[peak_idx])

    if mode == "piecewise-linear":
        num_points = 3
    elif mode in ("quadratic-polynomial-3-points", "quadratic-polynomial-5-points"):
        num_points = 3 if "3" in mode else 5
    else:
        return float(peak_idx), peak_max_val

    if num_points % 2 == 0:
        num_points += 1

    half_span = (num_points - 1) // 2
    x_values = np.arange(-half_span, half_span + 1, dtype=int)

    # Extend array circularly to handle boundaries
    # Pad with (num_points - 1) elements at start and num_points at end
    pad_start = num_points - 1
    caf_ext = np.concatenate([caf_1d[-pad_start:], caf_1d, caf_1d[:num_points]])
    # The offset is the number of elements added at the start
    y_values = caf_ext[x_values + peak_idx + pad_start]
    y_values = np.abs(y_values.astype(float))

    if mode == "piecewise-linear":
        left = y_values[0]
        right = y_values[-1]
        denom = peak_max_val - min(left, right)
        if denom <= 0:
            CpEstIdxError = 0.0
        else:
            CpEstIdxError = 0.5 * (right - left) / denom
        CpEstIdxInterp = peak_idx + CpEstIdxError
        peak_slope = peak_max_val - min(left, right)
        peakMaxValInterp = peak_max_val + abs(peak_slope * CpEstIdxInterp - peak_idx * peak_slope)
    else:
        x = x_values.astype(float)
        y = y_values.astype(float)
        M = np.column_stack((x**2, x, np.ones_like(x)))
        try:
            coeffs, *_ = np.linalg.lstsq(M, y, rcond=None)
            a, b, c = coeffs
        except np.linalg.LinAlgError:
            return float(peak_idx), peak_max_val

        if abs(a) < 1e-12:
            CpEstIdxError = 0.0
            peakMaxValInterp = peak_max_val
        else:
            CpEstIdxError = -b / (2.0 * a)
            peakMaxValInterp = float(a * CpEstIdxError**2 + b * CpEstIdxError + c)

        CpEstIdxInterp = peak_idx + CpEstIdxError
        if peakMaxValInterp < peak_max_val:
            peakMaxValInterp = peak_max_val

    # Clamp interpolated index
    if CpEstIdxInterp > peak_idx + 1.0:
        CpEstIdxInterp = peak_idx + 1.0
    elif CpEstIdxInterp < peak_idx - 1.0:
        CpEstIdxInterp = peak_idx - 1.0

    if peakMaxValInterp < peak_max_val:
        peakMaxValInterp = peak_max_val

    return float(CpEstIdxInterp), float(peakMaxValInterp)
