"""
Visualization functions for correlation analysis.

Includes correlation plots and E1B vs E6B peak comparison plots.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

from .constants import (
    E6_CHIP_RATE_HZ, E6_CODE_LENGTH_CHIPS,
    E1_CHIP_RATE_HZ, E1_CODE_LENGTH_CHIPS,
    F_CARRIER_E1, F_CARRIER_E6, SPEED_OF_LIGHT,
)
from .metrics import interpolate_caf_peak_1d


def _check_numpy() -> None:
    """Verify numpy is available."""
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for signal correlation but is not installed")


def _debug(msg: str) -> None:
    """Debug logging function."""
    try:
        from utils.utils import console_log
        console_log("SignalCorrelation", msg)
    except ImportError:
        print(f"[SignalCorrelation] {msg}")


def generate_e1_e6_peak_comparison_matplotlib(
    correlation_profiles: dict,
    prn: int,
    fs_hz: float,
    output_path: Optional[str] = None,
    window_samples: int = 50,
    figsize: Tuple[float, float] = (12, 7),
    show_plot: bool = False,
) -> Optional[Tuple[str, dict]]:
    """
    Generate E1-B vs E6-B correlation peaks comparison plot.
    
    This function creates a publication-quality plot similar to Figure 4 in the paper
    "Evaluation Platform for E6-B/C Signal Authentication" (Eng. Proc. 2023).
    
    CRITICAL: E1B is used as the REFERENCE (centered at 0).
    E6B uses the SAME window (based on E1B peak position), NOT centered on its own peak.
    This shows the REAL offset between peaks due to:
    - Ionospheric differential delay (frequency-dependent)
    - Broadcast Group Delay (BGD) - satellite hardware
    - Receiver Hardware Bias (HWB) - different SDR boards
    
    The BOC(1,1) central peak of E1-B is slightly wider but comparable to the 
    BPSK(5) peak of E6-B, as expected from the Autocorrelation Function (ACF).
    
    Args:
        correlation_profiles: Dict with correlation power profiles from correlation
        prn: PRN/SVID number to plot
        fs_hz: Sample rate in Hz
        output_path: Optional path to save the figure (PNG format)
        window_samples: Half-window size around E1B peak (default 50)
        figsize: Figure size in inches (width, height)
        show_plot: If True, display the plot interactively
    
    Returns:
        Tuple (path_to_saved_figure, metrics_dict) or None if error
        metrics_dict contains: peak_offset_samples, peak_offset_meters, etc.
    
    Reference:
        "Evaluation Platform for E6-B/C Signal Authentication",
        Eng. Proc. 2023, Figure 4
    """
    _check_numpy()
    
    try:
        import matplotlib.pyplot as plt
        import matplotlib.ticker as ticker
    except ImportError:
        _debug("matplotlib not available for plot generation")
        return None
    
    e1b_key = f"E1B_PRN{prn}"
    e6b_key = f"E6B_PRN{prn}"
    
    if e1b_key not in correlation_profiles or e6b_key not in correlation_profiles:
        _debug(f"Both E1B and E6B profiles required for PRN {prn}")
        return None
    
    # Extract profiles
    e1b_profile = correlation_profiles[e1b_key]
    e6b_profile = correlation_profiles[e6b_key]
    
    e1b_power = e1b_profile["power"]
    e6b_power = e6b_profile["power"]
    
    e1b_peak_idx = e1b_profile.get("peak_idx", int(np.argmax(e1b_power)))
    e6b_peak_idx_global = e6b_profile.get("peak_idx", int(np.argmax(e6b_power)))
    
    # =========================================================================
    # Find E6B peak CLOSEST to E1B peak (handle periodic code repetitions)
    # E6B code period: 5115 chips @ 5.115 MHz = 1 ms = 20000 samples @ 20 MHz
    # =========================================================================
    e6b_code_period_samples = int(fs_hz * 0.001)  # 1 ms period
    half_period = e6b_code_period_samples // 2
    
    # Search window around E1B peak position
    search_start = max(0, e1b_peak_idx - half_period)
    search_end = min(len(e6b_power), e1b_peak_idx + half_period)
    
    if search_start < search_end:
        search_region = e6b_power[search_start:search_end]
        local_peak_idx = int(np.argmax(search_region))
        e6b_peak_idx = search_start + local_peak_idx
    else:
        e6b_peak_idx = e6b_peak_idx_global
    
    _debug(f"PRN {prn}: E6B global peak @ {e6b_peak_idx_global}, aligned peak @ {e6b_peak_idx}")
    
    # =========================================================================
    # E1B is the REFERENCE - window centered on E1B peak
    # E6B uses the SAME window (NOT centered on its own peak!)
    # =========================================================================
    
    # E1B window
    e1b_start = max(0, e1b_peak_idx - window_samples)
    e1b_end = min(len(e1b_power), e1b_peak_idx + window_samples)
    e1b_window = e1b_power[e1b_start:e1b_end]
    
    # E6B uses SAME window indices as E1B
    e6b_start = e1b_start
    e6b_end = e1b_end
    
    if e6b_end <= len(e6b_power) and e6b_start >= 0:
        e6b_window = e6b_power[e6b_start:e6b_end]
    else:
        _debug(f"E6B window out of range for PRN {prn}")
        return None
    
    # X-axis: relative to E1B peak (E1B centered at 0)
    x_axis = np.arange(len(e1b_window)) - window_samples
    
    # Normalize each to [0, 1]
    e1b_normalized = e1b_window / np.max(e1b_window) if np.max(e1b_window) > 0 else e1b_window
    e6b_normalized = e6b_window / np.max(e6b_window) if np.max(e6b_window) > 0 else e6b_window
    
    # Calculate where E6B peak actually falls relative to E1B
    e6b_peak_in_window = e6b_peak_idx - e1b_peak_idx
    
    # Convert to physical units
    peak_offset_seconds = e6b_peak_in_window / fs_hz
    peak_offset_meters = peak_offset_seconds * SPEED_OF_LIGHT
    
    _debug(f"PRN {prn}: E1B peak @ {e1b_peak_idx}, E6B peak @ {e6b_peak_idx}")
    _debug(f"E6B offset from E1B: {e6b_peak_in_window} samples = {peak_offset_meters:.2f} m")
    
    # =========================================================================
    # Create plot (style similar to paper Figure 4)
    # =========================================================================
    plt.style.use('default')
    fig, ax = plt.subplots(figsize=figsize, facecolor='white')
    
    # Subsample for cleaner markers (~50-60 points like in the paper)
    total_points = len(x_axis)
    target_points = 55
    step = max(1, total_points // target_points)
    
    x_plot = x_axis[::step]
    e6b_plot = e6b_normalized[::step]
    e1b_plot = e1b_normalized[::step]
    
    # Plot E6-B (red) - first in legend like paper
    ax.plot(x_plot, e6b_plot, 'o-', 
            color='#E74C3C',
            markersize=6, 
            markerfacecolor='white',
            markeredgecolor='#E74C3C',
            markeredgewidth=1.5,
            linewidth=1.3,
            label='E6-B')
    
    # Plot E1-B (blue) - REFERENCE, centered at 0
    ax.plot(x_plot, e1b_plot, 'o-',
            color='#3498DB',
            markersize=6,
            markerfacecolor='white', 
            markeredgecolor='#3498DB',
            markeredgewidth=1.5,
            linewidth=1.3,
            label='E1-B (ref)')
    
    # Mark E1B peak at 0 (reference)
    ax.axvline(x=0, color='#3498DB', linestyle=':', alpha=0.4, linewidth=1)
    
    # Mark E6B peak where it actually falls
    if abs(e6b_peak_in_window) < window_samples:
        ax.axvline(x=e6b_peak_in_window, color='#E74C3C', linestyle=':', alpha=0.4, linewidth=1)
    
    # Configure axes
    ax.set_xlabel('Samples from E1B peak (reference)', fontsize=13, fontweight='medium')
    ax.set_ylabel('Normalised correlation value', fontsize=13, fontweight='medium')
    
    # Grid (like MATLAB/paper style)
    ax.grid(True, linestyle='--', alpha=0.6, color='#AAAAAA', linewidth=0.8)
    ax.set_axisbelow(True)
    
    # Set axis limits
    ax.set_ylim([0, 1.08])
    ax.set_xlim([-window_samples, window_samples])
    
    ax.tick_params(axis='both', labelsize=11)
    
    # Y-axis ticks
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    
    # Legend (upper right like paper)
    ax.legend(loc='upper right', fontsize=11, framealpha=0.95, 
              edgecolor='#CCCCCC', fancybox=True)
    
    # Add annotation with offset info
    offset_text = f'E6B offset: {e6b_peak_in_window} samples ({peak_offset_meters:.2f} m)'
    ax.annotate(offset_text, 
                xy=(0.02, 0.98), xycoords='axes fraction',
                fontsize=10, color='#555555',
                verticalalignment='top',
                bbox=dict(boxstyle='round,pad=0.3', facecolor='white', 
                         edgecolor='#CCCCCC', alpha=0.9))
    
    plt.tight_layout()
    
    # Metrics dictionary
    metrics = {
        'prn': prn,
        'e1b_peak_idx': e1b_peak_idx,
        'e6b_peak_idx': e6b_peak_idx,
        'e6b_offset_samples': e6b_peak_in_window,
        'e6b_offset_seconds': peak_offset_seconds,
        'e6b_offset_meters': peak_offset_meters,
        'fs_hz': fs_hz,
    }
    
    # Save figure
    saved_path = None
    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight', 
                    facecolor='white', edgecolor='none')
        saved_path = output_path
        _debug(f"E1-E6 comparison plot saved to: {output_path}")
    
    if show_plot:
        plt.show()
    else:
        plt.close(fig)
    
    return (saved_path, metrics) if saved_path else None


def generate_e1_e6_peak_comparison_centered(
    correlation_profiles: dict,
    prn: int,
    fs_hz: float,
    output_path: Optional[str] = None,
    window_samples: int = 200,
    figsize: Tuple[float, float] = (10, 6),
    show_plot: bool = False,
) -> Optional[str]:
    """
    Generate E1-B vs E6-C correlation peaks comparison plot (centered at peak).
    
    Alternative version where both peaks are centered at x=0, useful for
    comparing the autocorrelation function (ACF) shapes directly.
    
    Args:
        correlation_profiles: Dict with correlation power profiles from correlation
        prn: PRN/SVID number to plot
        fs_hz: Sample rate in Hz
        output_path: Optional path to save the figure (PNG format)
        window_samples: Half-window size around peak for display (default 200)
        figsize: Figure size in inches (width, height)
        show_plot: If True, display the plot interactively
    
    Returns:
        Path to saved figure if output_path provided, None otherwise
    """
    _check_numpy()
    
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        _debug("matplotlib not available for plot generation")
        return None
    
    e1b_key = f"E1B_PRN{prn}"
    e6c_key = f"E6C_PRN{prn}"
    
    if e1b_key not in correlation_profiles or e6c_key not in correlation_profiles:
        _debug(f"Both E1B and E6C profiles required for PRN {prn}")
        return None
    
    # Extract profiles
    e1b_profile = correlation_profiles[e1b_key]
    e6c_profile = correlation_profiles[e6c_key]
    
    e1b_power = e1b_profile["power"]
    e6c_power = e6c_profile["power"]
    
    e1b_peak_idx = e1b_profile.get("peak_idx", int(np.argmax(e1b_power)))
    e6c_peak_idx = e6c_profile.get("peak_idx", int(np.argmax(e6c_power)))
    
    # Extract windows centered at respective peaks
    e1b_start = max(0, e1b_peak_idx - window_samples)
    e1b_end = min(len(e1b_power), e1b_peak_idx + window_samples)
    e1b_window = e1b_power[e1b_start:e1b_end]
    
    e6c_start = max(0, e6c_peak_idx - window_samples)
    e6c_end = min(len(e6c_power), e6c_peak_idx + window_samples)
    e6c_window = e6c_power[e6c_start:e6c_end]
    
    # Normalize to [0, 1]
    e1b_normalized = e1b_window / np.max(e1b_window) if np.max(e1b_window) > 0 else e1b_window
    e6c_normalized = e6c_window / np.max(e6c_window) if np.max(e6c_window) > 0 else e6c_window
    
    # X-axis centered at 0 (peak position)
    e1b_x = np.arange(len(e1b_window)) - (e1b_peak_idx - e1b_start)
    e6c_x = np.arange(len(e6c_window)) - (e6c_peak_idx - e6c_start)
    
    # Create plot
    plt.style.use('default')
    fig, ax = plt.subplots(figsize=figsize, facecolor='white')
    
    # Subsample for markers
    step_e1b = max(1, len(e1b_x) // 50)
    step_e6c = max(1, len(e6c_x) // 50)
    
    ax.plot(e6c_x[::step_e6c], e6c_normalized[::step_e6c], 'o-',
            color='#E74C3C', markersize=5, markerfacecolor='white',
            markeredgecolor='#E74C3C', markeredgewidth=1.5,
            linewidth=1.2, label='E6-C')
    
    ax.plot(e1b_x[::step_e1b], e1b_normalized[::step_e1b], 'o-',
            color='#3498DB', markersize=5, markerfacecolor='white',
            markeredgecolor='#3498DB', markeredgewidth=1.5,
            linewidth=1.2, label='E1-B')
    
    ax.set_xlabel('Code phase delay (samples from peak)', fontsize=12)
    ax.set_ylabel('Normalised correlation value', fontsize=12)
    ax.grid(True, linestyle='--', alpha=0.7, color='gray')
    ax.set_axisbelow(True)
    ax.set_ylim([0, 1.05])
    ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
    ax.legend(loc='upper right', fontsize=10, framealpha=0.95)
    ax.axvline(x=0, color='gray', linestyle=':', alpha=0.5)  # Mark peak position
    
    plt.tight_layout()
    
    saved_path = None
    if output_path:
        fig.savefig(output_path, dpi=150, bbox_inches='tight',
                    facecolor='white', edgecolor='none')
        saved_path = output_path
        _debug(f"Centered comparison plot saved to: {output_path}")
    
    if show_plot:
        plt.show()
    else:
        plt.close(fig)
    
    return saved_path


def save_correlation_plot(
    results: list,
    iq_file_path: str,
    correlation_profiles: dict = None,
    fs_hz: float = None,
    recs_info: dict = None,
    ni_periods_e6: int = 1,
    ni_periods_aux: int = 1,
) -> Optional[str]:
    """
    Generate and save correlation plot with:
    1. Individual correlation profiles for each band (E1B, E1C, E6B, E6C)
    2. Peak comparison plot (E1B vs E6B centered and normalized)
    3. Summary tables with correlation metrics and E1B-E6B comparison
    
    Args:
        results: List of correlation results
        iq_file_path: Path to IQ file (for reference)
        correlation_profiles: Dict with correlation power profiles
        fs_hz: Sample rate in Hz
        recs_info: Dict with RECS info (ecs_path, recs_kdi, source)
        ni_periods_e6: Integration periods for E6B/E6C (Tint = Ni×1ms)
        ni_periods_aux: Integration periods for E1B (Tint = Ni×4ms)
    
    Returns:
        Path to saved HTML file, or None if failed
    """
    _check_numpy()
    
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        _debug("plotly not available for plot generation")
        return None
    
    from utils.utils import get_results_subdir
    
    if not correlation_profiles:
        _debug("No correlation profiles to plot")
        return None
    
    # Get unique PRNs from profiles
    prns = set()
    for key in correlation_profiles.keys():
        prn = int(key.split("PRN")[1])
        prns.add(prn)
    prns = sorted(prns)
    
    if not prns:
        return None
    
    # Determine which bands are available
    bands_available = set()
    for key in correlation_profiles.keys():
        band = key.split("_PRN")[0]
        bands_available.add(band)
    bands_order = ["E1B", "E1C", "E6B", "E6C"]
    bands_present = [b for b in bands_order if b in bands_available]
    
    num_bands = len(bands_present)
    num_prns = len(prns)
    
    # Colors for each band
    band_colors = {
        "E1B": "#3498db",  # Blue
        "E1C": "#2980b9",  # Dark Blue
        "E6B": "#e74c3c",  # Red
        "E6C": "#c0392b",  # Dark Red
    }
    
    # =========================================================================
    # FIGURE 1: Individual correlation profiles
    # =========================================================================
    subplot_titles = []
    for prn in prns:
        for band in bands_present:
            if band == "E1B":
                subtitle = f"PRN {prn} - E1B (Primary code, BOC(1,1))"
            elif band == "E1C":
                subtitle = f"PRN {prn} - E1C (Pilot code, BOC(1,1))"
            elif band == "E6B":
                subtitle = f"PRN {prn} - E6B (Primary code, periodic)"
            elif band == "E6C":
                profile = correlation_profiles.get(f"E6C_PRN{prn}", {})
                e6c_mode = profile.get("mode", "")
                if "encrypted" in e6c_mode.lower() or "ecs" in e6c_mode.lower():
                    subtitle = f"PRN {prn} - E6C (ECS from RECS)"
                else:
                    subtitle = f"PRN {prn} - E6C (Public code)"
            else:
                subtitle = f"PRN {prn} - {band}"
            subplot_titles.append(subtitle)
    
    total_rows = num_bands * num_prns
    # Ensure vertical_spacing doesn't exceed 1/(rows-1) for many rows
    max_spacing = 1.0 / max(total_rows - 1, 1)
    v_spacing = min(0.05, max_spacing * 0.9)
    fig1 = make_subplots(
        rows=total_rows, cols=1,
        subplot_titles=subplot_titles,
        vertical_spacing=v_spacing,
        shared_xaxes=True,
    )
    
    max_points = 50000
    max_display_samples = 2000000
    
    row_idx = 0
    for prn in prns:
        for band in bands_present:
            row_idx += 1
            key = f"{band}_PRN{prn}"
            
            if key not in correlation_profiles:
                continue
            
            profile = correlation_profiles[key]
            power = profile["power"]
            peak_idx = profile.get("peak_idx", np.argmax(power))
            
            # Get band-specific chip rate
            if band.startswith("E1"):
                chip_rate = E1_CHIP_RATE_HZ
                code_length = E1_CODE_LENGTH_CHIPS
            else:
                chip_rate = E6_CHIP_RATE_HZ
                code_length = E6_CODE_LENGTH_CHIPS
            
            samples_per_chip = fs_hz / chip_rate if fs_hz else 1.0
            
            # Limit display samples
            power = power[:max_display_samples]
            num_samples = len(power)
            
            # Normalize power to dB relative to noise floor
            noise_floor = np.median(power)
            power_db = 10 * np.log10(power / noise_floor + 1e-10)
            
            # Downsample for plotting using MAX in each block to preserve peaks
            if num_samples > max_points:
                step = num_samples // max_points
                n_blocks = num_samples // step
                power_db_blocks = power_db[:n_blocks * step].reshape(n_blocks, step)
                power_db_plot = np.max(power_db_blocks, axis=1)
                max_indices = np.argmax(power_db_blocks, axis=1)
                delay_samples_plot = np.arange(n_blocks) * step + max_indices
            else:
                delay_samples_plot = np.arange(num_samples)
                power_db_plot = power_db
            
            # SNR
            snr_like = None
            for r in results:
                if r.get("prn") == prn and r.get("signal") == band:
                    snr_like = r.get("snr_like")
                    break
            snr_output_db = 10 * np.log10(snr_like) if snr_like is not None and snr_like > 0 else float('-inf')

            # Peak value in dB for marker
            peak_power_db = 10 * np.log10(power[peak_idx] / noise_floor + 1e-10) if peak_idx < len(power) else 0

            # Add correlation trace
            fig1.add_trace(
                go.Scatter(
                    x=delay_samples_plot,
                    y=power_db_plot,
                    mode="lines",
                    name=f"{band} PRN {prn} | SNR: {snr_output_db:.2f} dB",
                    line=dict(color=band_colors.get(band, "#95a5a6"), width=0.8),
                    legendgroup=f"PRN{prn}",
                ),
                row=row_idx, col=1,
            )

            # Add peak marker
            fig1.add_trace(
                go.Scatter(
                    x=[peak_idx],
                    y=[peak_power_db],
                    mode="markers",
                    name=f"{band} Peak",
                    marker=dict(color="#27ae60", size=10, symbol="star"),
                    legendgroup=f"PRN{prn}",
                    showlegend=False,
                    hovertext=f"Peak @ {peak_idx} samples ({peak_power_db:.1f} dB) | SNR: {snr_output_db:.2f} dB",
                ),
                row=row_idx, col=1,
            )
            
            # Add code period markers for periodic signals
            if band != "E6C":
                samples_per_code = int(code_length * samples_per_chip)
                for period in range(1, min(10, int(num_samples // samples_per_code) + 1)):
                    sample_mark = period * samples_per_code
                    if sample_mark < num_samples:
                        fig1.add_vline(
                            x=sample_mark, 
                            line_dash="dot", 
                            line_color="gray", 
                            opacity=0.3,
                            row=row_idx, col=1,
                        )
            
            fig1.update_yaxes(
                title_text="Power [dB]",
                title_font=dict(size=18),
                tickfont=dict(size=14),
                row=row_idx, col=1,
            )

    fig1.update_xaxes(
        title_text="Delay [samples]",
        title_font=dict(size=18),
        tickfont=dict(size=14),
        row=total_rows, col=1,
    )
    fig1.update_xaxes(tickfont=dict(size=14))

    for ann in fig1["layout"]["annotations"]:
        ann["font"] = dict(size=18)

    fig1.update_layout(
        height=300 * total_rows,
        title_text="Multi-Band Signal Correlation (E1B, E1C, E6B, E6C)",
        title_font=dict(size=22),
        font=dict(size=14),
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.08,  # Moved higher to avoid overlap with title
            xanchor="center",
            x=0.5,
            font=dict(size=14),
        ),
        margin=dict(l=60, r=50, t=140, b=60),  # Increased top margin for legend
    )
    
    # =========================================================================
    # Build summary data and comparison data
    # =========================================================================
    summary_rows = []
    comparison_rows = []
    
    for prn in prns:
        prn_data = {}
        
        # Process all bands: E1B, E6B, E6C
        for band in ["E1B", "E6B", "E6C"]:
            result = None
            for r in results:
                if r.get("prn") == prn and r.get("signal") == band:
                    result = r
                    break
            
            if result is None:
                continue
            
            # Band-specific parameters
            if band.startswith("E1"):
                chip_rate = E1_CHIP_RATE_HZ
                code_length = E1_CODE_LENGTH_CHIPS
                t_int_ms = ni_periods_aux * 4.0  # E1B: Ni_aux × 4ms code period
            else:  # E6
                chip_rate = E6_CHIP_RATE_HZ
                code_length = E6_CODE_LENGTH_CHIPS
                t_int_ms = ni_periods_e6 * 1.0  # E6: Ni_e6 × 1ms code period
            
            doppler_hz = result.get("best_doppler_hz", 0)
            peak_idx = result.get("peak_index", 0)
            peak_idx_interp = result.get("peak_index_interp", float(peak_idx))
            
            snr_db = result.get("snr_output_db", float('-inf'))
            ppsp_db = result.get("ppsp_db", 0)
            cn0_dbhz = result.get("cn0_dbhz", float('-inf'))
            
            code_delay_samples = peak_idx_interp
            samples_per_chip = fs_hz / chip_rate if fs_hz else 1.0
            code_delay_chips = code_delay_samples / samples_per_chip
            
            summary_rows.append({
                "band": band, "prn": prn, "t_int_ms": t_int_ms,
                "doppler_hz": doppler_hz, "code_delay_samples": code_delay_samples,
                "code_delay_chips": code_delay_chips, 
                "snr_db": snr_db,
                "ppsp_db": ppsp_db,
                "cn0_dbhz": cn0_dbhz,
            })
            
            prn_data[band] = {
                "doppler_hz": doppler_hz,
                "code_delay_samples": code_delay_samples,
                "peak_idx_interp": peak_idx_interp,
            }
        
        # Calculate comparison metrics for SAME satellite (E1B vs E6C)
        # Use same method as peak comparison plot: find E6C peak closest to E1B peak
        if "E1B" in prn_data and "E6C" in prn_data:
            e1b = prn_data["E1B"]
            e6c = prn_data["E6C"]
            
            doppler_e1b = e1b["doppler_hz"]
            doppler_e6c = e6c["doppler_hz"]
            doppler_e6c_theoretical = doppler_e1b * (F_CARRIER_E6 / F_CARRIER_E1)
            doppler_error = doppler_e6c - doppler_e6c_theoretical
            
            # Get E1B peak position (interpolated)
            e1b_key = f"E1B_PRN{prn}"
            e6c_key = f"E6C_PRN{prn}"
            
            if e1b_key in correlation_profiles and e6c_key in correlation_profiles:
                e1b_power = correlation_profiles[e1b_key]["power"]
                e6c_power = correlation_profiles[e6c_key]["power"]
                e1b_peak_idx = correlation_profiles[e1b_key].get("peak_idx", int(np.argmax(e1b_power)))
                
                # Find E6C peak CLOSEST to E1B peak (within one code period)
                # E6C code period: 5115 chips @ 5.115 MHz = 1 ms = 20000 samples @ 20 MHz
                e6c_code_period_samples = int(E6_CODE_LENGTH_CHIPS * fs_hz / E6_CHIP_RATE_HZ)
                search_half = e6c_code_period_samples // 2
                search_start = max(0, e1b_peak_idx - search_half)
                search_end = min(len(e6c_power), e1b_peak_idx + search_half)
                
                if search_end > search_start:
                    e6c_search_window = e6c_power[search_start:search_end]
                    local_max_idx = np.argmax(e6c_search_window)
                    e6c_peak_idx_aligned = search_start + local_max_idx
                else:
                    e6c_peak_idx_aligned = correlation_profiles[e6c_key].get("peak_idx", int(np.argmax(e6c_power)))
                
                # Interpolate for sub-sample precision
                e1b_peak_interp, _ = interpolate_caf_peak_1d(e1b_power, mode="quadratic-polynomial-3-points", peak_idx=e1b_peak_idx)
                e6c_peak_interp, _ = interpolate_caf_peak_1d(e6c_power, mode="quadratic-polynomial-3-points", peak_idx=e6c_peak_idx_aligned)
                
                # Range error: E6C - E1B (positive = E6C arrives later)
                range_error_samples = e6c_peak_interp - e1b_peak_interp
                range_error_m = range_error_samples / fs_hz * SPEED_OF_LIGHT
                range_error_us = range_error_samples / fs_hz * 1e6
                
                code_delay_e1b_samples = e1b_peak_interp
                code_delay_e6c_samples = e6c_peak_interp
            else:
                # Fallback to simple calculation
                code_delay_e1b_samples = e1b["code_delay_samples"]
                code_delay_e6c_samples = e6c["code_delay_samples"]
                range_error_samples = code_delay_e6c_samples - code_delay_e1b_samples
                range_error_m = range_error_samples / fs_hz * SPEED_OF_LIGHT
                range_error_us = range_error_samples / fs_hz * 1e6
            
            iono_ratio = (F_CARRIER_E1 / F_CARRIER_E6) ** 2
            
            comparison_rows.append({
                "prn": prn,
                "doppler_e1b_hz": doppler_e1b,
                "doppler_e6c_hz": doppler_e6c,
                "doppler_e6c_theo_hz": doppler_e6c_theoretical,
                "doppler_error_hz": doppler_error,
                "delay_e1b_samples": code_delay_e1b_samples,
                "delay_e6c_samples": code_delay_e6c_samples,
                "range_error_samples": range_error_samples,
                "range_error_m": range_error_m,
                "range_error_us": range_error_us,
                "iono_ratio": iono_ratio,
            })
    
    # =========================================================================
    # FIGURE 2: E1B vs E6B Peak Comparison (paper Figure 4 style)
    # =========================================================================
    _debug(f"Available correlation profiles: {list(correlation_profiles.keys())}")
    _debug(f"PRNs to check: {prns}")
    
    num_comparison_plots = len([p for p in prns if f"E1B_PRN{p}" in correlation_profiles and f"E6B_PRN{p}" in correlation_profiles])
    _debug(f"Number of E1B vs E6B comparison plots: {num_comparison_plots}")
    
    fig2_html = ""
    if num_comparison_plots > 0:
        max_sp2 = 1.0 / max(num_comparison_plots - 1, 1)
        v_sp2 = min(0.15, max_sp2 * 0.9)
        fig2 = make_subplots(
            rows=num_comparison_plots, cols=1,
            subplot_titles=[f"PRN {prn} - E1B vs E6B Peak Comparison" for prn in prns 
                           if f"E1B_PRN{prn}" in correlation_profiles and f"E6B_PRN{prn}" in correlation_profiles],
            vertical_spacing=v_sp2,
        )
        
        plot_row = 0
        for prn in prns:
            e1b_key = f"E1B_PRN{prn}"
            e6b_key = f"E6B_PRN{prn}"
            
            if e1b_key not in correlation_profiles or e6b_key not in correlation_profiles:
                continue
            
            plot_row += 1
            
            e1b_profile = correlation_profiles[e1b_key]
            e1b_power = e1b_profile["power"]
            e1b_peak_idx = e1b_profile.get("peak_idx", int(np.argmax(e1b_power)))
            
            e6b_profile = correlation_profiles[e6b_key]
            e6b_power = e6b_profile["power"]
            
            # Find E6B peak closest to E1B peak
            e6b_code_period_samples = int(E6_CODE_LENGTH_CHIPS * fs_hz / E6_CHIP_RATE_HZ)
            search_half = e6b_code_period_samples // 2
            search_start = max(0, e1b_peak_idx - search_half)
            search_end = min(len(e6b_power), e1b_peak_idx + search_half)
            
            if search_end > search_start:
                e6b_search_window = e6b_power[search_start:search_end]
                local_max_idx = np.argmax(e6b_search_window)
                e6b_peak_idx_aligned = search_start + local_max_idx
            else:
                e6b_peak_idx_aligned = e6b_profile.get("peak_idx", int(np.argmax(e6b_power)))
            
            _debug(f"PRN {prn}: E1B peak @ {e1b_peak_idx}, E6B aligned peak @ {e6b_peak_idx_aligned}")
            
            # E1B as REFERENCE - window centered on E1B peak
            window_half_samples = 50
            
            e1b_start = max(0, e1b_peak_idx - window_half_samples)
            e1b_end = min(len(e1b_power), e1b_peak_idx + window_half_samples)
            e1b_window = e1b_power[e1b_start:e1b_end]
            
            e6b_start = e1b_start
            e6b_end = e1b_end
            
            if e6b_end <= len(e6b_power) and e6b_start >= 0:
                e6b_window = e6b_power[e6b_start:e6b_end]
            else:
                continue
            
            x_axis = np.arange(len(e1b_window)) - window_half_samples
            
            e1b_normalized = e1b_window / np.max(e1b_window) if np.max(e1b_window) > 0 else e1b_window
            e6b_normalized = e6b_window / np.max(e6b_window) if np.max(e6b_window) > 0 else e6b_window
            
            # Range error with sub-sample precision
            e1b_peak_interp, _ = interpolate_caf_peak_1d(e1b_power, mode="quadratic-polynomial-3-points", peak_idx=e1b_peak_idx)
            e6b_peak_interp, _ = interpolate_caf_peak_1d(e6b_power, mode="quadratic-polynomial-3-points", peak_idx=e6b_peak_idx_aligned)
            
            range_error_samples = e6b_peak_interp - e1b_peak_interp
            range_error_m = range_error_samples / fs_hz * SPEED_OF_LIGHT
            
            _debug(f"PRN {prn}: E1B peak @ {e1b_peak_interp:.3f}, E6B peak @ {e6b_peak_interp:.3f}, Range Error: {range_error_samples:.3f} samples = {range_error_m:.2f} m")
            
            # Add E6-B trace (red)
            fig2.add_trace(
                go.Scatter(
                    x=x_axis, y=e6b_normalized,
                    mode="lines+markers",
                    name=f"E6-B",
                    line=dict(color="#e74c3c", width=1.5),
                    marker=dict(size=5, symbol='circle-open'),
                    legendgroup=f"PRN{prn}",
                ),
                row=plot_row, col=1,
            )
            
            # Add E1-B trace (blue)
            fig2.add_trace(
                go.Scatter(
                    x=x_axis, y=e1b_normalized,
                    mode="lines+markers",
                    name=f"E1-B",
                    line=dict(color="#3498db", width=1.5),
                    marker=dict(size=5, symbol='circle-open'),
                    legendgroup=f"PRN{prn}",
                ),
                row=plot_row, col=1,
            )
            
            # Reference lines
            fig2.add_vline(x=0, line_dash="dot", line_color="#3498db", 
                          opacity=0.5, row=plot_row, col=1)
            
            e6b_peak_visual = e6b_peak_idx_aligned - e1b_peak_idx
            if abs(e6b_peak_visual) < window_half_samples:
                fig2.add_vline(x=e6b_peak_visual, line_dash="dot", line_color="#e74c3c", 
                              opacity=0.5, row=plot_row, col=1)
            
            # Range error annotation
            range_error_us = range_error_samples / fs_hz * 1e6
            fig2.add_annotation(
                x=0.02, y=0.95, xref="x domain", yref="y domain",
                text=f"<b>Range Error:</b> {range_error_samples:.3f} samples | {range_error_m:.2f} m | {range_error_us:.4f} μs",
                showarrow=False, font=dict(size=11, color="#333"),
                bgcolor="rgba(255,255,255,0.9)", bordercolor="#e74c3c", borderwidth=1,
                align="left",
                row=plot_row, col=1
            )
            
            fig2.update_yaxes(title_text="Normalised correlation value", row=plot_row, col=1)
            fig2.update_xaxes(title_text="Samples from E1B peak (reference)", row=plot_row, col=1,
                             range=[-window_half_samples, window_half_samples])
        
        fig2.update_layout(
            height=500 * num_comparison_plots,
            title_text="E1B vs E6B Peak Comparison (Real Positions)",
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
            margin=dict(l=60, r=50, t=100, b=60),
        )
        
        fig2_html = fig2.to_html(include_plotlyjs=False, full_html=False)
    
    # =========================================================================
    # Create HTML tables and output
    # =========================================================================
    tables_css = _get_tables_css()
    
    # RECS info box
    recs_info_html = ""
    if recs_info:
        ecs_path = recs_info.get("ecs_path", "")
        recs_kdi = recs_info.get("recs_kdi")
        source = recs_info.get("source", "ConfigInit")
        
        if ecs_path:
            ecs_filename = Path(ecs_path).name if ecs_path else "Unknown"
            kdi_text = f"KDI={recs_kdi}" if recs_kdi is not None else "KDI=auto"
            recs_info_html = f"""
    <div class="recs-info-box">
        <strong>🔐 ECS Used:</strong> {ecs_filename} 
        <span class="kdi-badge">{kdi_text}</span>
        <br><small style="color: #6c757d;">Source: {source}</small>
    </div>
    """
    
    # Build tables
    summary_table_html = _build_summary_table(summary_rows)
    comparison_table_html = _build_comparison_table(comparison_rows)
    
    # Use execution-specific results directory if available
    try:
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            plots_dir = ctx.get_execution_results_dir("plots")
        else:
            plots_dir = get_results_subdir("plots")
    except ImportError:
        plots_dir = get_results_subdir("plots")
    
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    plot_filename = f"correlation_{timestamp}.html"
    plot_path = plots_dir / plot_filename
    
    # Get HTML for figure 1
    fig1_html = fig1.to_html(include_plotlyjs=True, full_html=False)
    
    # Build comparison section
    has_e1_data = any(f"E1B_PRN{p}" in correlation_profiles for p in prns)
    has_e6b_data = any(f"E6B_PRN{p}" in correlation_profiles for p in prns)
    has_comparison_data = len(comparison_rows) > 0
    
    comparison_section = ""
    if has_comparison_data:
        comparison_section = comparison_table_html
    if fig2_html and has_e1_data and has_e6b_data:
        comparison_section += f"""
    <div class="section-title">🔬 E1B vs E6B Peak Comparison</div>
    {fig2_html}
"""
    
    # Generate MATLAB-style PNG plots for E1B vs E6B
    matplotlib_plots = []
    for prn in prns:
        e1b_key = f"E1B_PRN{prn}"
        e6b_key = f"E6B_PRN{prn}"
        
        if e1b_key in correlation_profiles and e6b_key in correlation_profiles:
            png_path = plots_dir / f"e1b_e6b_comparison_PRN{prn}_{timestamp}.png"
            result = generate_e1_e6_peak_comparison_matplotlib(
                correlation_profiles, prn, fs_hz,
                output_path=str(png_path),
                window_samples=60,
                show_plot=False
            )
            if result:
                saved_path, metrics = result
                matplotlib_plots.append(saved_path)
                _debug(f"PRN {prn} offset: {metrics['e6b_offset_samples']} samples = {metrics['e6b_offset_meters']:.2f} m")
    
    # Build complete HTML
    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Correlation Results</title>
    {tables_css}
</head>
<body>
    {recs_info_html}
    {summary_table_html}
    
    <div class="section-title">📈 Individual Correlation Profiles</div>
    {fig1_html}
    {comparison_section}
</body>
</html>
"""
    
    with open(plot_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    
    _debug(f"Plot saved to: {plot_path}")
    
    if matplotlib_plots:
        _debug(f"MATLAB-style PNG plots saved: {matplotlib_plots}")
    
    return str(plot_path)


def _get_tables_css() -> str:
    """Return CSS for HTML tables."""
    return """
    <style>
        .summary-table, .comparison-table {
            font-family: 'Segoe UI', Arial, sans-serif;
            border-collapse: collapse;
            width: 100%;
            max-width: 1000px;
            margin: 20px auto;
            background: #fff;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-radius: 8px;
            overflow: hidden;
        }
        .summary-table th, .comparison-table th {
            background: linear-gradient(180deg, #3498db 0%, #2980b9 100%);
            color: white;
            padding: 12px 10px;
            text-align: center;
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .summary-table td, .comparison-table td {
            padding: 10px;
            text-align: center;
            border-bottom: 1px solid #e8e8e8;
            font-size: 13px;
        }
        .summary-table tr:hover, .comparison-table tr:hover {
            background: #f5f9fc;
        }
        .table-title {
            text-align: center;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 16px;
            font-weight: 600;
            color: #2c3e50;
            margin: 25px 0 8px 0;
        }
        .section-title {
            text-align: center;
            font-family: 'Segoe UI', Arial, sans-serif;
            font-size: 20px;
            font-weight: 700;
            color: #2c3e50;
            margin: 40px 0 20px 0;
            padding-top: 20px;
            border-top: 2px solid #bdc3c7;
        }
        .recs-info-box {
            background: linear-gradient(135deg, #f8f9fa 0%, #e9ecef 100%);
            border: 1px solid #dee2e6;
            border-radius: 8px;
            padding: 12px 20px;
            margin: 15px auto;
            max-width: 600px;
            text-align: center;
            font-family: 'Segoe UI', Arial, sans-serif;
        }
        .recs-info-box strong {
            color: #2c3e50;
        }
        .recs-info-box .kdi-badge {
            display: inline-block;
            background: #3498db;
            color: white;
            padding: 2px 8px;
            border-radius: 4px;
            font-size: 12px;
            margin-left: 5px;
        }
    </style>
    """


def _build_summary_table(summary_rows: List[Dict]) -> str:
    """Build summary table HTML."""
    html = """
    <div class="table-title">📊 Correlation Summary</div>
    <table class="summary-table">
        <thead>
            <tr>
                <th>Band</th><th>PRN</th><th>T<sub>int</sub> [ms]</th>
                <th>Doppler [Hz]</th><th>Code Delay [samples]</th><th>Code Delay [chips]</th>
                <th>SNR [dB]</th><th>PPSP [dB]</th><th>C/N<sub>0</sub> [dB-Hz]</th>
            </tr>
        </thead>
        <tbody>
    """
    for row in summary_rows:
        cn0_str = f"{row['cn0_dbhz']:.2f}" if row['cn0_dbhz'] > float('-inf') else "-∞"
        snr_str = f"{row['snr_db']:.2f}" if row['snr_db'] > float('-inf') else "-∞"
        html += f"""
            <tr>
                <td><strong>{row["band"]}</strong></td><td><strong>{row["prn"]}</strong></td>
                <td>{row["t_int_ms"]:.1f}</td><td>{row["doppler_hz"]:.1f}</td>
                <td>{row["code_delay_samples"]:.3f}</td><td>{row["code_delay_chips"]:.3f}</td>
                <td>{snr_str}</td><td>{row["ppsp_db"]:.2f}</td><td>{cn0_str}</td>
            </tr>
        """
    html += "</tbody></table>"
    return html


def _build_comparison_table(comparison_rows: List[Dict]) -> str:
    """Build comparison table HTML."""
    html = """
    <div class="table-title">📐 E1B vs E6C Comparison (Same Satellite)</div>
    <table class="comparison-table">
        <thead>
            <tr>
                <th>PRN</th>
                <th>Doppler E1B [Hz]</th>
                <th>Doppler E6C [Hz]</th>
                <th>Doppler E6C Theo [Hz]</th>
                <th>Doppler Error [Hz]</th>
                <th>Delay E1B [samples]</th>
                <th>Delay E6C [samples]</th>
                <th>Range Error</th>
            </tr>
        </thead>
        <tbody>
    """
    for row in comparison_rows:
        html += f"""
            <tr>
                <td><strong>{row["prn"]}</strong></td>
                <td>{row["doppler_e1b_hz"]:.1f}</td>
                <td>{row["doppler_e6c_hz"]:.1f}</td>
                <td>{row["doppler_e6c_theo_hz"]:.1f}</td>
                <td>{row["doppler_error_hz"]:.1f}</td>
                <td>{row["delay_e1b_samples"]:.3f}</td>
                <td>{row["delay_e6c_samples"]:.3f}</td>
                <td>{row["range_error_samples"]:.3f} samples | {row["range_error_m"]:.2f} m | {row["range_error_us"]:.4f} μs</td>
            </tr>
        """
    html += """</tbody></table>
    <p style="text-align: center; font-size: 11px; color: #7f8c8d; margin-top: 5px;">
        Doppler E6C Theo = Doppler E1B × (f<sub>E6</sub>/f<sub>E1</sub>) = Doppler E1B × 0.8117 | 
        Range Error = Delay<sub>E1B</sub> - Delay<sub>E6C</sub> (includes iono + BGD + HWB)
    </p>
    """
    return html
