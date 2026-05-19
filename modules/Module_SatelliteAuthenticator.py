#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union

import numpy as np

try:
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots
    PLOTLY_AVAILABLE = True
except ImportError:
    PLOTLY_AVAILABLE = False

from utils.utils import (
    coerce_bool,
    coerce_float,
    coerce_int,
    coerce_string,
    load_json_file,
    save_json_file,
    console_log,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    RESULTS_DIR,
    get_results_subdir,
)

################################################ CONSTANTS ################################################

# -----------------------------------------------------------------------------
# Galileo Carrier Frequencies (Hz)
# Used for Doppler scaling (Eq. 19) and ionospheric correction (Eq. 17-18)
# f6/f1 ratio determines the ionospheric differential delay factor
# -----------------------------------------------------------------------------
F1_HZ = 1575.42e6  # E1 carrier frequency (L1 band)
F6_HZ = 1278.75e6  # E6 carrier frequency

# -----------------------------------------------------------------------------
# Chip Rates (Hz) - Determine code resolution and ranging precision
# E6 has 5x higher chip rate than E1, providing better multipath rejection
# -----------------------------------------------------------------------------
E1_CHIP_RATE_HZ = 1.023e6   # E1B/C: 1.023 Mchips/s (BOC(1,1) modulation)
E6_CHIP_RATE_HZ = 5.115e6   # E6B/C: 5.115 Mchips/s (BPSK(5) modulation)

# -----------------------------------------------------------------------------
# Code Lengths (chips per code period)
# E1B: 4092 chips = 4ms period (primary code, data channel)
# E6B: 5115 chips = 1ms period (pilot channel, can be encrypted)
# -----------------------------------------------------------------------------
E1_CODE_LENGTH_CHIPS = 4092   # E1B primary code length → 4ms period
E6B_CODE_LENGTH_CHIPS = 5115  # E6B/C code length → 1ms period

# -----------------------------------------------------------------------------
# Chip Lengths in Meters - Fundamental ranging resolution
# chip_length = c / chip_rate, where c = speed of light
# E6 provides ~5x better resolution than E1
# -----------------------------------------------------------------------------
E1_CHIP_METERS = 299792458.0 / E1_CHIP_RATE_HZ  # ~293.05 m per E1 chip
E6_CHIP_METERS = 299792458.0 / E6_CHIP_RATE_HZ  # ~58.61 m per E6 chip

# Speed of light in vacuum (m/s) - WGS84 value
SPEED_OF_LIGHT = 299792458.0

# -----------------------------------------------------------------------------
# MEASUREMENT ERROR MODEL - Paper TAES 2023, Table 1 and Equation (24)
# -----------------------------------------------------------------------------
# σ²_auth = σ²_HWBrx + σ²_BGD + σ²_I,E1 × ((f1²/f6²) - 1)² 
#           + σ²_MP,E1 + σ²_MP,E6 + σ²_n,E1 + σ²_n,E6
#
# IMPORTANT: The σ values represent RESIDUAL uncertainty AFTER corrections.
# The deterministic biases (BGD, iono, HWB_rx) must be CORRECTED first (Eq. 14),
# and the σ values model only the remaining uncertainty from that correction.
#
# Error sources (default values - WITHOUT BGD/ionospheric correction):
#   σ_HWBrx  = 0.0 m   Hardware bias E1/E6 (0 if calibrated or auto-calibrated)
#   σ_BGD    = 1.5 m   BGD uncertainty (0.0 with BGD file, 1.5 without)
#   σ_I,E1   = 15.0 m  Ionospheric error (15m without correction, 3.5m w/ NeQuick-G)
#   σ_MP,E1  = 3.0 m   E1 multipath (BOC(1,1), urban environment)
#   σ_MP,E6  = 0.6 m   E6 multipath (BPSK(5), sharper correlation peak)
#   σ_n,E1   = 0.4 m   E1 thermal noise (continuous tracking, C/N0=37 dB-Hz)
#                       NOTE: For snapshot E1B correlation, use ~3.0-5.0 m
#   σ_n,E6   = 2.5 m   E6 snapshot correlation noise
#   ─────────────────────────────────────────────────────────────────────────
#   σ_auth   ≈ 9.2 m   Total (RSS of all components, without corrections)
#
# With κ=3 (99.73% confidence): γ_auth ≈ 27.6 m
#
# If you have BGD file and NeQuick-G + auto-calibrate, use:
#   σ_BGD = 0.0 m, σ_I,E1 = 3.5 m, σ_n,E1 = 3.0 m (snapshot)
#   → σ_auth ≈ 5.5 m, γ_auth ≈ 16.5 m
# -----------------------------------------------------------------------------


# ─────────────────────────────────────────────────────────────────────────────
# Schema / Config loaders
# ─────────────────────────────────────────────────────────────────────────────

def load_schema() -> Dict[str, Any]:
    """Load the JSON schema for SatelliteAuthenticator block."""
    return load_json_file(SCHEMA_DIR / "SatelliteAuthenticator_schema.json")


def load_config() -> Dict[str, Any]:
    """Load the current configuration for SatelliteAuthenticator block."""
    return load_json_file(CONFIG_DIR / "SatelliteAuthenticator.json")


def save_config(config: Dict[str, Any]) -> None:
    """Save the configuration for SatelliteAuthenticator block."""
    save_json_file(CONFIG_DIR / "SatelliteAuthenticator.json", config)


def get_defaults() -> Dict[str, Any]:
    """Extract default values from the schema."""
    schema = load_schema()
    defaults = {}
    for section in schema.get("sections", []):
        for field in section.get("fields", []):
            defaults[field["id"]] = field.get("default")
    return defaults


# ─────────────────────────────────────────────────────────────────────────────
# Authentication Result Dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AuthenticationResult:
    """Result of satellite authentication for a single PRN."""
    prn: int
    authenticated: bool
    confidence_level: float  # percentage (e.g., 99.7 for κ=3)
    
    # Delays in samples
    code_delay_e1b_samples: float
    code_delay_e6c_samples: float
    
    # Delays in meters
    code_delay_e1b_meters: float
    code_delay_e6c_meters: float
    
    # Delta (τ_E6 - τ̂_E6)
    delta_samples: float
    delta_meters: float
    
    # Thresholds (in samples - primary unit)
    gamma_auth_samples: float  # γ_auth = κ × σ_auth [samples]
    sigma_auth_samples: float  # σ_auth from equation (24) [samples]
    # Thresholds (in meters - for reference)
    gamma_auth_meters: float  # γ_auth = κ × σ_auth [m]
    sigma_auth_meters: float  # σ_auth from equation (24) [m]
    kappa: float              # confidence factor
    
    # Signal quality metrics
    snr_e1b_db: float
    snr_e6c_db: float
    ppsp_e1b_db: float
    ppsp_e6c_db: float
    
    # Status message
    status: str
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# Core Authentication Functions
# ─────────────────────────────────────────────────────────────────────────────

def calculate_sigma_auth(
    sigma_hwbrx_m: float,
    sigma_bgd_m: float,
    sigma_iono_e1_m: float,
    sigma_mp_e1_m: float,
    sigma_mp_e6_m: float,
    sigma_noise_e1_m: float,
    sigma_noise_e6_m: float,
) -> float:
    """
    Calculate the total authentication sigma (σ_auth) using Equation (24) from paper.
    
    This implements the measurement error model from Section III.E and the Appendix:
    
        σ²_auth = σ²_HWBrx + σ²_BGD + σ²_I,E1 × ((f1²/f6²) - 1)² 
                  + σ²_MP,E1 + σ²_MP,E6 + σ²_n,E1 + σ²_n,E6
    
    The ionospheric term includes the frequency scaling factor from Eq. (17-18):
        δI_E1,E6 = I_E1 × ((f1²/f6²) - 1)
    
    This factor arises because ionospheric delay is proportional to 1/f², so the
    differential delay between E1 and E6 depends on their frequency ratio.
    
    Args:
        sigma_hwbrx_m: Receiver hardware bias uncertainty [m]
        sigma_bgd_m: Broadcast Group Delay uncertainty [m]
        sigma_iono_e1_m: Ionospheric error on E1 after correction [m]
        sigma_mp_e1_m: E1 multipath error [m]
        sigma_mp_e6_m: E6 multipath error [m]
        sigma_noise_e1_m: E1 thermal noise error [m]
        sigma_noise_e6_m: E6 thermal noise error [m]
        
    Returns:
        σ_auth: Total authentication standard deviation in meters
    
    Example:
        With default values: σ_auth = 4.72 m
        With κ=3: γ_auth = 14.16 m (99.73% confidence)
    """
    # Ionospheric frequency correction factor from Eq. (18): (f1²/f6² - 1)
    # f1 = 1575.42 MHz, f6 = 1278.75 MHz
    # (f1/f6)² = 1.518, so factor = 0.518
    # This means the E1-E6 ionospheric differential is ~52% of the E1 iono error
    freq_ratio_squared = (F1_HZ / F6_HZ) ** 2
    iono_factor = freq_ratio_squared - 1  # ≈ 0.518
    
    # Equation (24): Root Sum Square of all error variances
    # Each term represents an independent error source (assumed uncorrelated)
    sigma_squared = (
        sigma_hwbrx_m ** 2 +                      # Hardware bias variance
        sigma_bgd_m ** 2 +                        # BGD variance
        (sigma_iono_e1_m * iono_factor) ** 2 +    # Ionospheric differential variance
        sigma_mp_e1_m ** 2 +                      # E1 multipath variance
        sigma_mp_e6_m ** 2 +                      # E6 multipath variance
        sigma_noise_e1_m ** 2 +                   # E1 noise variance
        sigma_noise_e6_m ** 2                     # E6 noise variance
    )
    
    return math.sqrt(sigma_squared)


def calculate_gamma_auth(sigma_auth_m: float, kappa: float) -> float:
    """
    Calculate authentication threshold γ_auth = κ × σ_auth.
    
    Args:
        sigma_auth_m: Total sigma in meters
        kappa: Confidence factor (2 for 95%, 3 for 99.7%, 4 for 99.99%)
        
    Returns:
        γ_auth in meters
    """
    return kappa * sigma_auth_m


def get_confidence_percentage(kappa: float) -> float:
    """
    Get the confidence percentage for a given kappa value.
    
    Uses standard normal distribution:
        κ=1 → 68.27%
        κ=2 → 95.45%
        κ=3 → 99.73%
        κ=4 → 99.99%
    """
    # Approximate confidence levels for standard normal distribution
    confidence_table = {
        1.0: 68.27,
        1.5: 86.64,
        2.0: 95.45,
        2.5: 98.76,
        3.0: 99.73,
        3.5: 99.95,
        4.0: 99.99,
    }
    
    # Find closest
    closest_kappa = min(confidence_table.keys(), key=lambda x: abs(x - kappa))
    return confidence_table.get(closest_kappa, 99.73)


def samples_to_meters(samples: float, sample_rate_hz: float, band: str = "E6") -> float:
    """
    Convert code delay in samples to meters.
    
    Args:
        samples: Number of samples
        sample_rate_hz: Sample rate in Hz
        band: "E1" or "E6" for chip rate selection
        
    Returns:
        Distance in meters
    """
    chip_rate = E1_CHIP_RATE_HZ if band == "E1" else E6_CHIP_RATE_HZ
    samples_per_chip = sample_rate_hz / chip_rate
    chips = samples / samples_per_chip
    chip_meters = E1_CHIP_METERS if band == "E1" else E6_CHIP_METERS
    return chips * chip_meters


def meters_to_samples(meters: float, sample_rate_hz: float, band: str = "E6") -> float:
    """
    Convert distance in meters to samples.
    
    Args:
        meters: Distance in meters
        sample_rate_hz: Sample rate in Hz
        band: "E1" or "E6" for chip rate selection
        
    Returns:
        Number of samples
    """
    chip_meters = E1_CHIP_METERS if band == "E1" else E6_CHIP_METERS
    chips = meters / chip_meters
    chip_rate = E1_CHIP_RATE_HZ if band == "E1" else E6_CHIP_RATE_HZ
    samples_per_chip = sample_rate_hz / chip_rate
    return chips * samples_per_chip


def authenticate_measurement(
    code_delay_e1b_samples: float,
    code_delay_e6c_samples: float,
    sample_rate_hz: float,
    gamma_auth_m: float,
    sigma_auth_m: float,
    kappa: float,
    delta_e1_e6_samples: float = 0.0,
    snr_e1b_db: float = 0.0,
    snr_e6c_db: float = 0.0,
    ppsp_e1b_db: float = 0.0,
    ppsp_e6c_db: float = 0.0,
    prn: int = 0,
) -> AuthenticationResult:
    """
    Perform measurement authentication for a single PRN satellite.
    
    This is the core Galileo SAS authentication algorithm implementing Equations (20)-(23)
    from the paper "Semiassisted Signal Authentication for Galileo".
    
    Algorithm Overview:
    ══════════════════
    
    1. ESTIMATE expected E6 code delay from E1 (Equation 20):
       τ̂_E6 = τ_E1 + δ_E1,E6
       
       Where δ_E1,E6 (Equation 14) compensates for:
       - BGD (Broadcast Group Delay): Satellite hardware bias between E1/E6
       - δI_E1,E6: Ionospheric differential delay (I_E1 × 0.518)
       - HWB_rx: Receiver hardware bias (different RF chains)
    
    2. COMPARE measured vs expected (Equation 21):
       Δ = |τ_E6_measured - τ̂_E6_estimated|
       
       If Δ ≤ γ_auth → AUTHENTICATED (ξ = 1)
       If Δ > γ_auth → NOT AUTHENTICATED (ξ = 0)
    
    Code Period Handling:
    ════════════════════
    E1B code is PERIODIC (4092 chips, 4ms period), meaning the correlation
    peak repeats every 4ms. The E6C ECS is APERIODIC (spans multiple ms).
    
    To correctly compute the delta, we must:
    1. Extract the E1B phase within its code period
    2. Extract the E6C phase within the same period
    3. Compare phases (not absolute positions) to handle periodicity
    4. Apply wrap-around correction for phases near period boundaries
    
    Args:
        code_delay_e1b_samples: E1B correlation peak position [samples]
        code_delay_e6c_samples: E6C correlation peak position [samples]
        sample_rate_hz: Sampling rate used for correlation [Hz]
        gamma_auth_m: Authentication threshold γ_auth = κ × σ_auth [meters]
        sigma_auth_m: Total authentication sigma σ_auth [meters]
        kappa: Confidence factor κ (2=95%, 3=99.7%, 4=99.99%)
        delta_e1_e6_samples: Systematic E1-E6 bias compensation [samples]
                             Obtained from calibration or estimated from:
                             BGD + ionospheric differential + hardware bias
        snr_e1b_db: E1B signal-to-noise ratio [dB]
        snr_e6c_db: E6C signal-to-noise ratio [dB]
        ppsp_e1b_db: E1B primary-to-secondary peak ratio [dB]
        ppsp_e6c_db: E6C primary-to-secondary peak ratio [dB]
        prn: PRN/SVID number for the satellite
        
    Returns:
        AuthenticationResult: Dataclass containing:
            - authenticated: Boolean result of the test
            - delta_samples/meters: Measured offset Δ
            - gamma_auth_samples/meters: Threshold used
            - confidence_level: Statistical confidence percentage
            - status: Human-readable result string
    
    Example:
        If E1B peak at sample 12345 and E6C peak at sample 12350:
        - E1B phase = 12345 mod 80000 = 12345 samples
        - E6C phase = 12350 mod 80000 = 12350 samples
        - Δ = 5 samples = 75m (at 20 MHz)
        - If γ_auth = 14.16m, then NOT AUTHENTICATED (75m > 14.16m)
    """
    # =========================================================================
    # STEP 1: Calculate E1B code period in samples
    # =========================================================================
    # E1B code: 4092 chips at 1.023 Mchips/s = 4ms period
    # At fs = 20 MHz: period = 20e6 × 4e-3 = 80,000 samples
    e1b_period_samples = sample_rate_hz / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS
    
    # =========================================================================
    # STEP 2: Extract phase within E1B period (handle code periodicity)
    # =========================================================================
    # E1B is periodic: the correlation peak repeats every 4ms
    # E6C (with ECS) is aperiodic: only one correlation peak in the snapshot
    # To compare them, we extract the phase (position within one code period)
    #
    # If both signals are authentic and synchronized:
    #   e1b_phase ≈ e6c_phase (within tolerance)
    #
    e1b_phase = code_delay_e1b_samples % e1b_period_samples
    e6c_phase = code_delay_e6c_samples % e1b_period_samples
    
    # =========================================================================
    # STEP 3: Apply E1-E6 bias compensation (Equation 20)
    # =========================================================================
    # τ̂_E6 = τ_E1 + δ_E1,E6
    #
    # The delta_e1_e6_samples term compensates for systematic biases:
    # - BGD: Broadcast Group Delay (satellite hardware)
    # - Ionospheric differential: I_E1 × ((f1²/f6²) - 1)
    # - Hardware bias: Different RF chains for E1 and E6
    #
    # After compensation, authentic signals should have near-zero delta
    e1b_phase_compensated = (e1b_phase + delta_e1_e6_samples) % e1b_period_samples
    
    # =========================================================================
    # STEP 4: Calculate delta Δ = τ_E6 - τ̂_E6 (Equation 21)
    # =========================================================================
    delta_samples = e6c_phase - e1b_phase_compensated
    
    # =========================================================================
    # STEP 5: Handle wrap-around at period boundaries
    # =========================================================================
    # If delta > half period, the shorter path is the other direction
    # Example: if E1B phase = 79000 and E6C phase = 1000,
    #          raw delta = -78000, but actual delta = +2000
    if delta_samples > e1b_period_samples / 2:
        delta_samples = delta_samples - e1b_period_samples
    elif delta_samples < -e1b_period_samples / 2:
        delta_samples = delta_samples + e1b_period_samples
    
    # =========================================================================
    # STEP 6: Convert delta to meters for reporting
    # =========================================================================
    # Δ_meters = Δ_samples × (c / fs)
    # At fs = 20 MHz: 1 sample ≈ 15 meters
    delta_time_s = delta_samples / sample_rate_hz
    delta_m = delta_time_s * SPEED_OF_LIGHT
    
    # Store pseudorange equivalents for reference (phase converted to range)
    delay_e1b_m = (e1b_phase / sample_rate_hz) * SPEED_OF_LIGHT
    delay_e6c_m = (e6c_phase / sample_rate_hz) * SPEED_OF_LIGHT
    
    # =========================================================================
    # STEP 7: Convert threshold to samples for comparison
    # =========================================================================
    # γ_auth_samples = γ_auth_meters × (fs / c)
    # Example: 14.16m at 20 MHz → 0.94 samples
    gamma_auth_samples = gamma_auth_m / SPEED_OF_LIGHT * sample_rate_hz
    sigma_auth_samples = sigma_auth_m / SPEED_OF_LIGHT * sample_rate_hz
    
    # =========================================================================
    # STEP 8: Apply Authentication Test (Equation 21)
    # =========================================================================
    # |τ_E6 - τ̂_E6| ≤ γ_auth  →  AUTHENTICATED (ξ = 1)
    # |τ_E6 - τ̂_E6| > γ_auth  →  NOT AUTHENTICATED (ξ = 0)
    authenticated = abs(delta_samples) <= gamma_auth_samples
    
    # Determine status
    if authenticated:
        status = f"AUTHENTICATED (|Δ|={abs(delta_m):.2f}m ≤ γ={gamma_auth_m:.2f}m)"
    else:
        status = f"NOT AUTHENTICATED (|Δ|={abs(delta_m):.2f}m > γ={gamma_auth_m:.2f}m)"
    
    confidence = get_confidence_percentage(kappa)
    
    # Build result
    result = AuthenticationResult(
        prn=prn,
        authenticated=authenticated,
        confidence_level=confidence,
        code_delay_e1b_samples=code_delay_e1b_samples,
        code_delay_e6c_samples=code_delay_e6c_samples,
        code_delay_e1b_meters=delay_e1b_m,
        code_delay_e6c_meters=delay_e6c_m,
        delta_samples=delta_samples,
        delta_meters=delta_m,
        gamma_auth_samples=gamma_auth_samples,
        sigma_auth_samples=sigma_auth_samples,
        gamma_auth_meters=gamma_auth_m,
        sigma_auth_meters=sigma_auth_m,
        kappa=kappa,
        snr_e1b_db=snr_e1b_db,
        snr_e6c_db=snr_e6c_db,
        ppsp_e1b_db=ppsp_e1b_db,
        ppsp_e6c_db=ppsp_e6c_db,
        status=status,
    )
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Visualization Functions
# ─────────────────────────────────────────────────────────────────────────────

# Publication-quality font for paper figures
_PAPER_FONT = dict(family="Times New Roman, serif", size=14, color="#1C1C1C")


def _export_publication_figures(
    fig,
    output_path: Path,
    base_name: str,
    num_prns: int = 1,
) -> None:
    """
    Export a Plotly figure in publication-quality formats: PDF, SVG, and
    high-resolution PNG (300 DPI).  Applies paper-friendly styling
    (serif font, white background, larger text) only to the exported
    copies, leaving the interactive HTML version untouched.
    """
    try:
        import kaleido  # noqa: F401
    except ImportError:
        console_log("SatelliteAuthenticator",
                     "kaleido not installed -- skipping PDF/SVG/PNG export. "
                     "Install with: pip install kaleido")
        return

    # Deep-copy layout tweaks for print (don't mutate the original fig)
    import copy
    pub_fig = copy.deepcopy(fig)

    # Paper-friendly dimensions (IEEE column ~ 3.5 in, full page ~ 7 in)
    width_px = 1400          # ~7 in @ 200 DPI -- fits full-width IEEE figure
    height_px = max(900, 750 * num_prns)

    pub_fig.update_layout(
        font=_PAPER_FONT,
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(t=100, b=70, l=80, r=30),
        width=width_px,
        height=height_px,
        legend=dict(
            font=dict(family="Times New Roman, serif", size=13, color="#1C1C1C"),
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor="rgba(0,0,0,0.25)", borderwidth=1,
        ),
    )

    # Ensure all axis labels use the paper font
    pub_fig.update_xaxes(
        title_font=dict(family="Times New Roman, serif", size=13, color="#1C1C1C"),
        tickfont=dict(family="Times New Roman, serif", size=11, color="#2C2C2C"),
        linecolor="#2C2C2C", linewidth=1.2,
        mirror=True, ticks="outside",
        gridcolor="rgba(0,0,0,0.08)",
    )
    pub_fig.update_yaxes(
        title_font=dict(family="Times New Roman, serif", size=13, color="#1C1C1C"),
        tickfont=dict(family="Times New Roman, serif", size=11, color="#2C2C2C"),
        linecolor="#2C2C2C", linewidth=1.2,
        mirror=True, ticks="outside",
        gridcolor="rgba(0,0,0,0.08)",
    )

    # Style subplot titles with paper font
    for ann in pub_fig['layout']['annotations']:
        if hasattr(ann, 'text') and ann.text:
            ann.font = dict(family="Times New Roman, serif", size=14, color="#1C1C1C")

    formats = {
        "pdf":  {"format": "pdf"},
        "svg":  {"format": "svg"},
        "png":  {"format": "png", "scale": 3},     # 3x -> ~600 DPI effective
        "tikz": None,  # handled separately below
    }

    for ext, kwargs in formats.items():
        if ext == "tikz":
            # TikZ export via tikzplotly (optional)
            try:
                import tikzplotly
                out_file = output_path / f"{base_name}.tex"
                tikzplotly.save(str(out_file), pub_fig)
                console_log("SatelliteAuthenticator", f"  TikZ saved: {out_file}")
            except ImportError:
                console_log("SatelliteAuthenticator",
                            "  tikzplotly not installed -- skipping TikZ export. "
                            "Install with: pip install tikzplotly")
            except Exception as exc:
                console_log("SatelliteAuthenticator", f"  TikZ export failed: {exc}")
            continue
        out_file = output_path / f"{base_name}.{ext}"
        try:
            pub_fig.write_image(str(out_file), width=width_px, height=height_px, **kwargs)
            console_log("SatelliteAuthenticator", f"  {ext.upper()} saved: {out_file}")
        except Exception as exc:
            console_log("SatelliteAuthenticator", f"  {ext.upper()} export failed: {exc}")


def generate_e1b_e6c_peak_comparison(
    correlation_profiles: Dict[str, Any],
    auth_results: List[Dict[str, Any]],
    gamma_auth_m: float,
    sigma_auth_m: float,
    sample_rate_hz: float,
    output_path: Optional[Path] = None,
    window_samples: int = 8,
    interp_factor: int = 50,
) -> Optional[str]:
    """
    Generate E1B vs E6C correlation peak comparison plot for authentication.

    For each authenticated PRN, shows the E1B and E6C peaks from the *same*
    temporal period, zoomed in tightly around the peaks with cubic
    interpolation so that sub-sample offsets are clearly visible.

    Two rows per PRN:
      - Top: zoomed view (±window_samples) with interpolated curves
      - Bottom: wide overview (±60 samples) for context

    Args:
        correlation_profiles: Dict mapping e.g. 'E1B_PRN14' -> {power, fs_hz, peak_idx}
        auth_results: Per-PRN authentication results from run_pipeline()
        gamma_auth_m: Authentication threshold in meters
        sigma_auth_m: Total sigma in meters
        sample_rate_hz: Sample rate in Hz
        output_path: Directory to save the HTML plot
        window_samples: Half-window size for zoomed view (default 8)
        interp_factor: Interpolation factor for smooth curves (default 50)

    Returns:
        Path to saved HTML file, or None
    """
    if not PLOTLY_AVAILABLE:
        return None

    from modules.correlation.metrics import interpolate_caf_peak_1d
    from scipy.interpolate import CubicSpline

    valid = [r for r in auth_results if r.get("delta_meters") is not None]
    if not valid or not correlation_profiles:
        return None

    e1b_period_samples = int(sample_rate_hz / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS)
    gamma_samples = gamma_auth_m / SPEED_OF_LIGHT * sample_rate_hz
    sigma_samples = sigma_auth_m / SPEED_OF_LIGHT * sample_rate_hz

    # Two rows per PRN: zoomed + overview
    num_prns = len(valid)

    # Build per-PRN summary for the main title
    prn_summaries = []
    for r in valid:
        prn = r["prn"]
        delta_m = r["delta_meters"]
        status = "PASS" if r["authenticated"] else "FAIL"
        color = "#1E8449" if r["authenticated"] else "#C0392B"
        prn_summaries.append(
            f"<span style='color:{color}'>PRN {prn}: Δ={delta_m:+.2f} m ({status})</span>"
        )

    fig = make_subplots(
        rows=2 * num_prns, cols=1,
        vertical_spacing=0.10,
        row_heights=[4, 1] * num_prns,
    )

    for idx, r in enumerate(valid):
        prn = r["prn"]
        e1b_key = f"E1B_PRN{prn}"
        e6c_key = f"E6C_PRN{prn}"

        if e1b_key not in correlation_profiles or e6c_key not in correlation_profiles:
            continue

        e1b_power = np.asarray(correlation_profiles[e1b_key]["power"], dtype=np.float64)
        e6c_power = np.asarray(correlation_profiles[e6c_key]["power"], dtype=np.float64)

        # --- Determine which E1B period matches the E6C peak ---
        e6c_peak_global = int(np.argmax(e6c_power))
        target_period = e6c_peak_global // e1b_period_samples
        p_start = target_period * e1b_period_samples
        p_end = min(p_start + e1b_period_samples, len(e1b_power))
        local_max_e1b = p_start + int(np.argmax(e1b_power[p_start:p_end]))

        e1b_center = local_max_e1b
        e6c_center = e6c_peak_global

        # E6C offset relative to E1B (in samples, within E1B period)
        e6c_phase = e6c_center % e1b_period_samples
        e1b_phase = e1b_center % e1b_period_samples
        offset = e6c_phase - e1b_phase
        if offset > e1b_period_samples / 2:
            offset -= e1b_period_samples
        elif offset < -e1b_period_samples / 2:
            offset += e1b_period_samples

        # Interpolated peak positions (sub-sample precision)
        e1b_peak_interp, _ = interpolate_caf_peak_1d(
            e1b_power, mode="quadratic-polynomial-3-points", peak_idx=e1b_center)
        e6c_peak_interp, _ = interpolate_caf_peak_1d(
            e6c_power, mode="quadratic-polynomial-3-points", peak_idx=e6c_center)

        # Sub-sample offset
        e6c_interp_phase = e6c_peak_interp % e1b_period_samples
        e1b_interp_phase = e1b_peak_interp % e1b_period_samples
        offset_interp = e6c_interp_phase - e1b_interp_phase
        if offset_interp > e1b_period_samples / 2:
            offset_interp -= e1b_period_samples
        elif offset_interp < -e1b_period_samples / 2:
            offset_interp += e1b_period_samples

        # ============ ROW 1: ZOOMED VIEW (interpolated) ============
        row_zoom = 2 * idx + 1
        w = window_samples

        # E1B: extract window, interpolate
        e1b_s = max(0, e1b_center - w - 2)
        e1b_e = min(len(e1b_power), e1b_center + w + 3)
        e1b_raw_x = np.arange(e1b_s, e1b_e) - e1b_center  # centred at 0
        e1b_raw_y = e1b_power[e1b_s:e1b_e]
        e1b_raw_norm = e1b_raw_y / (np.max(e1b_raw_y) + 1e-30)

        # Cubic spline interpolation for smooth curve
        cs_e1b = CubicSpline(e1b_raw_x, e1b_raw_norm)
        e1b_x_fine = np.linspace(-w, w, (2 * w) * interp_factor + 1)
        e1b_y_fine = np.clip(cs_e1b(e1b_x_fine), 0, None)

        # E6C: extract window around its peak, shift by offset
        e6c_s = max(0, e6c_center - w - 2)
        e6c_e = min(len(e6c_power), e6c_center + w + 3)
        e6c_raw_x = np.arange(e6c_s, e6c_e) - e6c_center + offset_interp
        e6c_raw_y = e6c_power[e6c_s:e6c_e]
        e6c_raw_norm = e6c_raw_y / (np.max(e6c_raw_y) + 1e-30)

        cs_e6c = CubicSpline(e6c_raw_x, e6c_raw_norm)
        e6c_x_fine = np.linspace(-w + offset_interp, w + offset_interp,
                                 (2 * w) * interp_factor + 1)
        e6c_y_fine = np.clip(cs_e6c(e6c_x_fine), 0, None)

        # E1B interpolated curve (blue)
        fig.add_trace(
            go.Scatter(
                x=e1b_x_fine.tolist(), y=e1b_y_fine.tolist(),
                mode="lines",
                name="E1B",
                line=dict(color="#1B6CA8", width=3),
                legendgroup=f"e1b_{prn}",
                showlegend=(idx == 0),
            ),
            row=row_zoom, col=1,
        )

        # E1B discrete samples (blue markers)
        e1b_disc_x = e1b_raw_x[(e1b_raw_x >= -w) & (e1b_raw_x <= w)]
        e1b_disc_y = e1b_raw_norm[(e1b_raw_x >= -w) & (e1b_raw_x <= w)]
        fig.add_trace(
            go.Scatter(
                x=e1b_disc_x.tolist(), y=e1b_disc_y.tolist(),
                mode="markers",
                name="E1B samples",
                marker=dict(size=8, color="white", symbol="circle",
                            line=dict(color="#1B6CA8", width=2)),
                legendgroup=f"e1b_{prn}",
                showlegend=False,
            ),
            row=row_zoom, col=1,
        )

        # E6C interpolated curve (coral red)
        fig.add_trace(
            go.Scatter(
                x=e6c_x_fine.tolist(), y=e6c_y_fine.tolist(),
                mode="lines",
                name="E6C (ECS)",
                line=dict(color="#C0392B", width=3),
                legendgroup=f"e6c_{prn}",
                showlegend=(idx == 0),
            ),
            row=row_zoom, col=1,
        )

        # E6C discrete samples (red markers)
        e6c_disc_mask = (e6c_raw_x >= -w - 1) & (e6c_raw_x <= w + 1)
        fig.add_trace(
            go.Scatter(
                x=e6c_raw_x[e6c_disc_mask].tolist(),
                y=e6c_raw_norm[e6c_disc_mask].tolist(),
                mode="markers",
                name="E6C samples",
                marker=dict(size=8, color="white", symbol="diamond",
                            line=dict(color="#C0392B", width=2)),
                legendgroup=f"e6c_{prn}",
                showlegend=False,
            ),
            row=row_zoom, col=1,
        )

        # Auth zone (green shaded band)
        fig.add_vrect(
            x0=-gamma_samples, x1=gamma_samples,
            fillcolor="rgba(39,174,96,0.10)",
            layer="below", line_width=0,
            row=row_zoom, col=1,
        )
        for bnd in (-gamma_samples, gamma_samples):
            fig.add_vline(
                x=bnd, line_dash="dash",
                line_color="#1E8449", line_width=1.5,
                opacity=0.6,
                row=row_zoom, col=1,
            )

        # (Delta annotation and arrow removed — no concrete values on plot)

        # Axis labels for zoomed view (no x-axis title to avoid overlap with wide view)
        fig.update_xaxes(
            title_text="",
            tickfont=dict(size=11, color="#34495E"),
            row=row_zoom, col=1,
            range=[-w, w],
            dtick=1,
            gridwidth=1, gridcolor="rgba(0,0,0,0.06)",
            zeroline=True, zerolinewidth=1.5, zerolinecolor="rgba(0,0,0,0.12)",
            minor=dict(dtick=0.5, showgrid=True,
                       gridcolor="rgba(0,0,0,0.03)"),
        )
        fig.update_yaxes(
            title_text="Normalised correlation power",
            title_font=dict(size=12, color="#2C3E50", family="Inter, Arial, sans-serif"),
            tickfont=dict(size=11, color="#34495E"),
            row=row_zoom, col=1,
            range=[-0.02, 1.12],
            gridwidth=1, gridcolor="rgba(0,0,0,0.06)",
        )

        # ============ ROW 2: WIDE OVERVIEW (minimal) ============
        row_wide = 2 * idx + 2
        w_wide = 60

        # E1B wide window
        e1b_ws = max(0, e1b_center - w_wide)
        e1b_we = min(len(e1b_power), e1b_center + w_wide + 1)
        e1b_wide_x = np.arange(e1b_ws, e1b_we) - e1b_center
        e1b_wide_y = e1b_power[e1b_ws:e1b_we]
        e1b_wide_norm = e1b_wide_y / (np.max(e1b_wide_y) + 1e-30)

        # E6C wide window
        e6c_ws = max(0, e6c_center - w_wide)
        e6c_we = min(len(e6c_power), e6c_center + w_wide + 1)
        e6c_wide_x = np.arange(e6c_ws, e6c_we) - e6c_center + offset_interp
        e6c_wide_y = e6c_power[e6c_ws:e6c_we]
        e6c_wide_norm = e6c_wide_y / (np.max(e6c_wide_y) + 1e-30)

        fig.add_trace(
            go.Scatter(
                x=e1b_wide_x.tolist(), y=e1b_wide_norm.tolist(),
                mode="lines",
                name="E1B wide",
                line=dict(color="#1B6CA8", width=1.2),
                legendgroup=f"e1b_{prn}",
                showlegend=False,
            ),
            row=row_wide, col=1,
        )
        fig.add_trace(
            go.Scatter(
                x=e6c_wide_x.tolist(), y=e6c_wide_norm.tolist(),
                mode="lines",
                name="E6C wide",
                line=dict(color="#C0392B", width=1.2, dash="dot"),
                legendgroup=f"e6c_{prn}",
                showlegend=False,
            ),
            row=row_wide, col=1,
        )

        fig.update_xaxes(
            title_text="Samples",
            title_font=dict(size=11, color="#2C3E50", family="Inter, Arial, sans-serif"),
            tickfont=dict(size=10, color="#34495E"),
            row=row_wide, col=1,
            range=[-w_wide, w_wide],
            gridwidth=1, gridcolor="rgba(0,0,0,0.04)",
        )
        fig.update_yaxes(
            title_text="Norm. corr.",
            title_font=dict(size=11, color="#2C3E50", family="Inter, Arial, sans-serif"),
            tickfont=dict(size=10, color="#34495E"),
            row=row_wide, col=1,
            range=[-0.02, 1.10],
            gridwidth=1, gridcolor="rgba(0,0,0,0.04)",
        )

    confidence_pct = get_confidence_percentage(
        gamma_auth_m / sigma_auth_m if sigma_auth_m else 3.0)

    prn_line = " · ".join(prn_summaries)
    fig.update_layout(
        title=dict(
            text=(
                f"<b>E1B vs E6C Peak Comparison (Galileo SAS Authentication)</b><br>"
                f"<span style='font-size:13px'>{prn_line}</span><br>"
                f"<span style='font-size:11px; color:#566573'>γ_auth = ±{gamma_samples:.2f} samples "
                f"({gamma_auth_m:.2f} m, {confidence_pct:.1f}% confidence)</span>"
            ),
            font=dict(size=16, color="#1C2833", family="Inter, Arial, sans-serif"),
            x=0.5, xanchor="center",
        ),
        height=max(700, 600 * num_prns),
        template="plotly_white",
        paper_bgcolor="#FDFEFE",
        plot_bgcolor="#FDFEFE",
        legend=dict(
            orientation="h", yanchor="top", y=-0.08,
            xanchor="center", x=0.5,
            font=dict(size=12, color="#2C3E50", family="Inter, Arial, sans-serif"),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="rgba(0,0,0,0.1)", borderwidth=1,
        ),
        margin=dict(t=100, b=80, l=65, r=30),
        hoverlabel=dict(
            bgcolor="white",
            font_size=12,
            font_family="Inter, Arial, sans-serif",
            bordercolor="rgba(0,0,0,0.15)",
        ),
    )

    # Save
    if output_path is None:
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            output_path = ctx.get_execution_results_dir("plots")
        else:
            output_path = get_results_subdir("authentication")
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"auth_peak_comparison_{timestamp}"

    # Interactive HTML
    plot_file = output_path / f"{base_name}.html"
    fig.write_html(str(plot_file), include_plotlyjs="cdn")
    console_log("SatelliteAuthenticator", f"E1B-E6C comparison plot saved to: {plot_file}")

    # Publication-quality static exports (PDF, SVG, PNG)
    _export_publication_figures(fig, output_path, base_name, num_prns)

    return str(plot_file)


def generate_authentication_plots(
    auth_results: List[Dict[str, Any]],
    gamma_auth_m: float,
    sigma_auth_m: float,
    kappa: float,
    output_path: Optional[Path] = None,
    correlation_profiles: Optional[Dict[str, Any]] = None,
    sample_rate_hz: float = 20e6,
) -> Optional[Tuple[str, Optional[str]]]:
    """
    Generate interactive authentication visualization plots.
    
    Creates up to three plots:
    1. Bar chart: Delta (Δ) per PRN with authentication threshold (γ_auth)
    2. Correlation alignment: E1B and E6C peaks aligned, showing auth zone
    3. E1B vs E6C peak comparison (if correlation_profiles available)
    
    Args:
        auth_results: List of per-PRN authentication results
        gamma_auth_m: Authentication threshold in meters
        sigma_auth_m: Total sigma in meters
        kappa: Confidence factor
        output_path: Path to save the HTML plot
        correlation_profiles: Correlation power profiles from SignalCorrelation
        sample_rate_hz: Sample rate in Hz
        
    Returns:
        Tuple (main_plot_path, comparison_plot_path) or None if Plotly not available
    """
    if not PLOTLY_AVAILABLE:
        console_log("SatelliteAuthenticator", "Plotly not available, skipping plots")
        return None
    
    # Filter results that have delta values (not skipped)
    valid_results = [r for r in auth_results if r.get("delta_meters") is not None]
    
    if not valid_results:
        console_log("SatelliteAuthenticator", "No valid results to plot")
        return None
    
    # Create subplot figure
    fig = make_subplots(
        rows=2, cols=1,
        subplot_titles=(
            "Authentication Delta per PRN (|τ_E6 - τ̂_E6|)",
            "E1B vs E6C Peak Alignment (within E1B period)"
        ),
        vertical_spacing=0.15,
        row_heights=[0.45, 0.55]
    )
    
    # ─────────────────────────────────────────────────────────────────────────
    # Plot 1: Bar chart of deltas with threshold
    # ─────────────────────────────────────────────────────────────────────────
    prns = [f"PRN {r['prn']}" for r in valid_results]
    deltas = [abs(r["delta_meters"]) for r in valid_results]
    colors = ["#27ae60" if r["authenticated"] else "#e74c3c" for r in valid_results]
    
    # Delta bars
    fig.add_trace(
        go.Bar(
            x=prns,
            y=deltas,
            marker_color=colors,
            name="Δ = |E6C - E1B|",
            text=[f"{d:.2f}m" for d in deltas],
            textposition="outside",
            hovertemplate="PRN %{x}<br>Δ = %{y:.2f} m<extra></extra>"
        ),
        row=1, col=1
    )
    
    # Authentication threshold line (γ_auth)
    fig.add_hline(
        y=gamma_auth_m,
        line_dash="dash",
        line_color="#3498db",
        line_width=2,
        annotation_text=f"γ_auth = {gamma_auth_m:.2f}m (κ={kappa})",
        annotation_position="top right",
        row=1, col=1
    )
    
    # Sigma lines for reference
    for k, label, color in [(1, "1σ", "#95a5a6"), (2, "2σ", "#7f8c8d")]:
        fig.add_hline(
            y=k * sigma_auth_m,
            line_dash="dot",
            line_color=color,
            line_width=1,
            annotation_text=f"{label} = {k*sigma_auth_m:.2f}m",
            annotation_position="bottom right",
            row=1, col=1
        )
    
    # ─────────────────────────────────────────────────────────────────────────
    # Plot 2: E1B vs E6C markers (aligned at peaks)
    # ─────────────────────────────────────────────────────────────────────────
    
    # E1B period in meters (for reference)
    sample_rate_hz = 20e6
    e1b_period_samples = sample_rate_hz / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS
    e1b_period_meters = (e1b_period_samples / sample_rate_hz) * SPEED_OF_LIGHT
    
    for i, r in enumerate(valid_results):
        prn = r["prn"]
        e1b_m = r["code_delay_e1b_meters"]
        e6c_m = r["code_delay_e6c_meters"]
        delta_m = r["delta_meters"]
        is_auth = r["authenticated"]
        
        # E1B marker (reference at 0)
        fig.add_trace(
            go.Scatter(
                x=[0],
                y=[0.5],
                mode="markers+text",
                marker=dict(size=15, color="#3498db", symbol="triangle-up"),
                text=["E1B"],
                textposition="top center",
                name=f"PRN{prn} E1B",
                showlegend=False,
                hovertemplate=f"PRN {prn} E1B<br>Phase: {e1b_m:.2f}m<extra></extra>"
            ),
            row=2, col=1
        )
        
        # E6C marker (with delta offset from E1B)
        color = "#27ae60" if is_auth else "#e74c3c"
        fig.add_trace(
            go.Scatter(
                x=[delta_m],
                y=[0.5],
                mode="markers+text",
                marker=dict(size=15, color=color, symbol="triangle-down"),
                text=["E6C"],
                textposition="bottom center",
                name=f"PRN{prn} E6C",
                showlegend=False,
                hovertemplate=f"PRN {prn} E6C<br>Δ: {delta_m:+.2f}m<extra></extra>"
            ),
            row=2, col=1
        )
    
    # Authentication zone (green band around 0)
    fig.add_vrect(
        x0=-gamma_auth_m,
        x1=gamma_auth_m,
        fillcolor="rgba(39, 174, 96, 0.15)",
        layer="below",
        line_width=0,
        row=2, col=1
    )
    
    # Threshold lines
    fig.add_vline(
        x=-gamma_auth_m,
        line_dash="dash",
        line_color="#27ae60",
        line_width=2,
        row=2, col=1
    )
    fig.add_vline(
        x=gamma_auth_m,
        line_dash="dash",
        line_color="#27ae60",
        line_width=2,
        row=2, col=1
    )
    
    # Center line (E1B reference at 0)
    fig.add_vline(
        x=0,
        line_dash="solid",
        line_color="#3498db",
        line_width=1,
        annotation_text="E1B peak",
        annotation_position="top",
        row=2, col=1
    )
    
    # ─────────────────────────────────────────────────────────────────────────
    # Layout
    # ─────────────────────────────────────────────────────────────────────────
    confidence_pct = get_confidence_percentage(kappa)
    
    fig.update_layout(
        title=dict(
            text=f"Satellite Authentication Analysis (Galileo SAS) - γ_auth = {gamma_auth_m:.2f}m ({confidence_pct:.1f}% confidence)",
            font=dict(size=16)
        ),
        height=800,
        showlegend=True,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="right",
            x=1
        ),
        template="plotly_white"
    )
    
    # Axis labels
    fig.update_xaxes(title_text="PRN", row=1, col=1)
    fig.update_yaxes(title_text="Delta |Δ| (meters)", row=1, col=1, range=[0, max(deltas) * 1.3 + gamma_auth_m])
    
    # X range for correlation plot: show ±3*gamma_auth around E1B peak (centered at 0)
    x_range_half = max(gamma_auth_m * 4, 60)  # At least ±60m or ±4*gamma
    fig.update_xaxes(
        title_text="Relative position (meters) - E1B peak centered at 0",
        row=2, col=1,
        range=[-x_range_half, x_range_half]
    )
    fig.update_yaxes(
        title_text="Normalized Power",
        row=2, col=1,
        range=[-0.05, 1.15]
    )
    
    # Add legend annotation for auth zone
    fig.add_annotation(
        x=gamma_auth_m * 2.5,
        y=0.9,
        text=f"<b>Auth Zone</b><br>±{gamma_auth_m:.2f}m",
        showarrow=False,
        font=dict(size=10, color="#27ae60"),
        bgcolor="rgba(39, 174, 96, 0.1)",
        bordercolor="#27ae60",
        borderwidth=1,
        xref="x2",
        yref="y2"
    )
    
    # Save plot
    if output_path is None:
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()
        if ctx.get_execution_id():
            output_path = ctx.get_execution_results_dir("plots")
        else:
            output_path = get_results_subdir("authentication")
    
    output_path = Path(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"authentication_plot_{timestamp}"
    plot_file = output_path / f"{base_name}.html"

    fig.write_html(str(plot_file), include_plotlyjs="cdn")
    console_log("SatelliteAuthenticator", f"Plot saved to: {plot_file}")

    # Publication-quality static exports (PDF, SVG, PNG)
    _export_publication_figures(fig, output_path, base_name)

    # ─────────────────────────────────────────────────────────────────────────
    # Plot 3: E1B vs E6C peak comparison (if correlation_profiles available)
    # ─────────────────────────────────────────────────────────────────────────
    comparison_plot_file = None
    if correlation_profiles:
        try:
            comparison_plot_file = generate_e1b_e6c_peak_comparison(
                correlation_profiles=correlation_profiles,
                auth_results=auth_results,
                gamma_auth_m=gamma_auth_m,
                sigma_auth_m=sigma_auth_m,
                sample_rate_hz=sample_rate_hz,
                output_path=output_path,
            )
        except Exception as e:
            console_log("SatelliteAuthenticator",
                       f"Warning: Could not generate E1B-E6C comparison: {e}")

    return str(plot_file), comparison_plot_file


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN PIPELINE FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def _run_pipeline_legacy(
    config: Optional[Mapping[str, Any]] = None,
    *,
    correlation_results: Optional[Dict[str, Any]] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Run the Galileo SAS Satellite Authenticator pipeline.
    
    This is the main entry point for authentication. It receives correlation
    results from SignalCorrelation and performs the Galileo SAS authentication test
    on each PRN that has both E1B and E6C (ECS) measurements.
    
    Pipeline Flow:
    ══════════════
    
    1. LOAD CONFIGURATION
       - Load σ values from config (or use paper defaults)
       - Calculate σ_auth using Equation (24)
       - Calculate γ_auth = κ × σ_auth using Equation (23)
    
    2. RECEIVER CALIBRATION (optional, enabled by default)
       - Estimate HWB_rx from correlation results
       - This compensates for systematic E1-E6 timing offset
    
    3. AUTHENTICATE EACH PRN
       - Skip PRNs without both E1B and E6C results
       - Skip PRNs using public E6C code (requires ECS)
       - Skip PRNs with SNR below threshold
       - Apply authentication test (Equation 21)
    
    4. POSITION AUTHENTICATION (Equation 22)
       - Position is authenticated if ALL PRNs pass the test
       - ∏(ξ_k) = 1 → Position Authenticated
    
    5. OUTPUT RESULTS
       - Save JSON results to execution directory
       - Generate visualization plots
    
    Args:
        config: Optional configuration mapping to override JSON config.
                Key parameters:
                - kappa: Confidence factor (default: 3.0)
                - sigma_*: Error model parameters
                - min_snr_db: Minimum SNR for authentication
                - auto_calibrate_offset: Enable receiver calibration
        correlation_results: Results dictionary from SignalCorrelation.run_pipeline()
                             Must contain 'e1_results' and 'e6_results' lists
        **kwargs: Additional arguments (forwarded but not used)
            
    Returns:
        Dictionary containing:
        - success: Boolean indicating pipeline completion
        - position_authenticated: Boolean result of Equation (22)
        - authenticated_count: Number of authenticated PRNs
        - failed_count: Number of failed PRNs
        - skipped_count: Number of skipped PRNs
        - prn_results: List of per-PRN AuthenticationResult dicts
        - calibration: Receiver offset calibration info
        - config: Configuration used (γ_auth, σ_auth, κ, etc.)
        - error_model: σ values used
        - summary: Human-readable summary message
    """
    # Load config from own JSON, then apply overrides
    base_config = load_config()
    if config:
        base_config.update(config)
    config = base_config
    
    # If correlation_results passed, merge into config
    if correlation_results:
        config["correlation_results"] = correlation_results
    
    defaults = get_defaults()
    
    # === Extract configuration ===
    kappa = coerce_float(config.get("kappa", defaults.get("kappa", 3.0)), 3.0)
    
    # Error model parameters (Table 1 from paper - defaults for NO BGD/iono correction)
    sigma_hwbrx = coerce_float(config.get("sigma_hwbrx_m", defaults.get("sigma_hwbrx_m")), 0.0)
    sigma_bgd = coerce_float(config.get("sigma_bgd_m", defaults.get("sigma_bgd_m")), 1.5)
    sigma_iono = coerce_float(config.get("sigma_iono_e1_m", defaults.get("sigma_iono_e1_m")), 15.0)
    sigma_mp_e1 = coerce_float(config.get("sigma_mp_e1_m", defaults.get("sigma_mp_e1_m")), 3.0)
    sigma_mp_e6 = coerce_float(config.get("sigma_mp_e6_m", defaults.get("sigma_mp_e6_m")), 0.6)
    sigma_noise_e1 = coerce_float(config.get("sigma_noise_e1_m", defaults.get("sigma_noise_e1_m")), 0.4)
    sigma_noise_e6 = coerce_float(config.get("sigma_noise_e6_m", defaults.get("sigma_noise_e6_m")), 2.5)
    
    # Signal quality thresholds
    min_snr_db = coerce_float(config.get("min_snr_db", defaults.get("min_snr_db", 10.0)), 10.0)
    min_ppsp_db = coerce_float(config.get("min_ppsp_db", defaults.get("min_ppsp_db", 5.0)), 5.0)
    
    # Manual receiver offset (in samples)
    # Per the Galileo SAS paper: the receiver hardware bias HWB_rx "should be calibrated"
    # This is a known constant for a given receiver, NOT auto-calculated from data.
    # Default is 0.0 (assumes a well-calibrated receiver)
    receiver_offset_samples = coerce_float(
        config.get("receiver_offset_samples", defaults.get("receiver_offset_samples", 0.0)),
        0.0
    )
    
    # Use custom sigma_auth or calculate from components (Eq.24)
    sigma_auth_override = config.get("sigma_auth_m")
    # Check for actual value (not empty string or None)
    if sigma_auth_override is not None and sigma_auth_override != "":
        sigma_auth = coerce_float(sigma_auth_override, 9.2)
    else:
        sigma_auth = calculate_sigma_auth(
            sigma_hwbrx, sigma_bgd, sigma_iono,
            sigma_mp_e1, sigma_mp_e6,
            sigma_noise_e1, sigma_noise_e6
        )
    
    gamma_auth = calculate_gamma_auth(sigma_auth, kappa)
    
    # Get correlation results
    correlation_results = config.get("correlation_results", {})
    e1_results = correlation_results.get("e1_results", [])
    e6_results = correlation_results.get("e6_results", [])
    sample_rate_hz = coerce_float(
        correlation_results.get("sample_rate_hz", config.get("sample_rate_hz", 20e6)),
        20e6
    )
    
    # Build lookup tables by PRN and signal
    e1b_by_prn = {}
    e6c_by_prn = {}
    
    for result in e1_results:
        if result.get("signal") == "E1B" and "error" not in result:
            prn = result.get("prn")
            if prn:
                e1b_by_prn[prn] = result
    
    for result in e6_results:
        if result.get("signal") == "E6C" and "error" not in result:
            prn = result.get("prn")
            if prn:
                e6c_by_prn[prn] = result
    
    # ─────────────────────────────────────────────────────────────────────────
    # E1-E6 Bias Compensation: δ_E1,E6 (Equation 14 from paper)
    # ─────────────────────────────────────────────────────────────────────────
    # The expected delta between E1B and E6C depends on (Eq. 14):
    #   δ_E1,E6 = BGD_E1,E6 + δI_E1,E6 + HWB_rx
    #
    # Where:
    #   - BGD_E1,E6: Broadcast Group Delay (satellite hardware, from BGD file)
    #   - δI_E1,E6: Ionospheric differential = I_E1 × (f1²/f6² - 1) ≈ 0.518 × I_E1
    #   - HWB_rx: Receiver hardware bias (different RF chains for E1 and E6)
    #
    # For snapshot receivers (like bladeRF), all three components must be
    # compensated BEFORE comparing against the threshold.
    #
    # When auto_calibrate_offset=true:
    #   Estimate the total δ_E1,E6 from the data (median of raw deltas across
    #   all PRNs). This captures HWB_rx + mean BGD + mean ionospheric bias.
    #   Useful for development/testing with uncalibrated receivers.
    #
    # When auto_calibrate_offset=false:
    #   Use the manual receiver_offset_samples from config.
    #   This should include the full δ_E1,E6 = BGD + iono + HWB_rx.
    # ─────────────────────────────────────────────────────────────────────────
    
    auto_calibrate = coerce_bool(
        config.get("auto_calibrate_offset", defaults.get("auto_calibrate_offset", True)),
        True
    )
    
    # Compute raw deltas for all valid PRN pairs (needed for auto-calibration)
    e1b_period_samples = sample_rate_hz / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS
    raw_deltas = {}  # prn → raw delta in samples
    
    for prn in sorted(set(e1b_by_prn.keys()) & set(e6c_by_prn.keys())):
        e1b = e1b_by_prn[prn]
        e6c = e6c_by_prn[prn]
        
        # Skip public code (not usable for authentication)
        e6c_mode = e6c.get("mode", "unknown")
        if "public" in e6c_mode.lower():
            continue
        
        # Prefer the same-period matched E1B peak (compensates code-Doppler
        # drift across the snapshot) when available.  Fall back to the global
        # peak for backward compatibility.
        delay_e1b = e1b.get("peak_index_interp_matched",
                           e1b.get("peak_index_interp", e1b.get("peak_index", 0)))
        delay_e6c = e6c.get("peak_index_interp", e6c.get("peak_index", 0))
        
        e1b_phase = delay_e1b % e1b_period_samples
        e6c_phase = delay_e6c % e1b_period_samples
        
        raw_delta = e6c_phase - e1b_phase
        if raw_delta > e1b_period_samples / 2:
            raw_delta -= e1b_period_samples
        elif raw_delta < -e1b_period_samples / 2:
            raw_delta += e1b_period_samples
        
        raw_deltas[prn] = raw_delta
    
    if auto_calibrate and raw_deltas:
        # Estimate combined bias as the median of raw deltas across all PRNs
        # This captures: HWB_rx + mean(BGD) + mean(δI)
        sorted_deltas = sorted(raw_deltas.values())
        n = len(sorted_deltas)
        if n % 2 == 1:
            estimated_bias_samples = sorted_deltas[n // 2]
        else:
            estimated_bias_samples = (sorted_deltas[n // 2 - 1] + sorted_deltas[n // 2]) / 2
        
        delta_e1_e6_samples = estimated_bias_samples
        estimated_bias_m = (estimated_bias_samples / sample_rate_hz) * SPEED_OF_LIGHT
        
        console_log("SatelliteAuthenticator",
                   f"Auto-calibration: estimated δ_E1,E6 = {estimated_bias_samples:.3f} samples "
                   f"= {estimated_bias_m:.2f}m (from {n} PRN pair(s))")
        
        if n == 1:
            console_log("SatelliteAuthenticator",
                       f"WARNING: Only 1 PRN pair available for calibration. "
                       f"Cannot distinguish receiver bias from spoofing. "
                       f"Consider setting receiver_offset_samples manually.")
        
        receiver_offset_info = {
            "method": "auto_calibrate",
            "offset_samples": delta_e1_e6_samples,
            "offset_meters": estimated_bias_m,
            "num_prn_pairs": n,
            "raw_deltas_samples": {str(k): v for k, v in raw_deltas.items()},
            "raw_deltas_meters": {str(k): (v / sample_rate_hz) * SPEED_OF_LIGHT for k, v in raw_deltas.items()},
            "note": (
                f"Auto-calibrated from {n} PRN pair(s). "
                f"To use as permanent calibration, set receiver_offset_samples={estimated_bias_samples:.4f} in config."
            ),
        }
    else:
        delta_e1_e6_samples = receiver_offset_samples
        receiver_offset_info = {
            "method": "manual",
            "offset_samples": receiver_offset_samples,
            "offset_meters": (receiver_offset_samples / sample_rate_hz) * SPEED_OF_LIGHT,
            "note": "Manual receiver offset (δ_E1,E6 per Galileo SAS paper Eq.14). Set via 'receiver_offset_samples' config."
        }
    
    # Authenticate each PRN that has both E1B and E6C results
    authentication_results: List[Dict[str, Any]] = []
    authenticated_count = 0
    failed_count = 0
    skipped_count = 0
    
    all_prns = set(e1b_by_prn.keys()) | set(e6c_by_prn.keys())
    
    for prn in sorted(all_prns):
        e1b = e1b_by_prn.get(prn)
        e6c = e6c_by_prn.get(prn)
        
        # Need both E1B and E6C for authentication
        if e1b is None or e6c is None:
            skipped_count += 1
            authentication_results.append({
                "prn": prn,
                "authenticated": None,
                "status": f"SKIPPED: Missing {'E1B' if e1b is None else 'E6C'} result",
                "e1b_available": e1b is not None,
                "e6c_available": e6c is not None,
            })
            continue
        
        # Check if E6C used encrypted code (ECS) - required for authentication
        # Galileo SAS authentication only works with the encrypted spreading code
        e6c_mode = e6c.get("mode", "unknown")
        if "public" in e6c_mode.lower():
            skipped_count += 1
            authentication_results.append({
                "prn": prn,
                "authenticated": None,
                "status": f"SKIPPED: E6C uses public code (requires ECS for Galileo SAS authentication)",
                "e6c_mode": e6c_mode,
            })
            console_log("SatelliteAuthenticator", 
                       f"PRN {prn}: Skipped - E6C uses public code. Add PRN to svid_encrypted for authentication.")
            continue
        
        # Check signal quality
        snr_e1b = e1b.get("snr_output_db", e1b.get("snr_db", 0.0))
        snr_e6c = e6c.get("snr_output_db", e6c.get("snr_db", 0.0))
        ppsp_e1b = e1b.get("ppsp_db", 0.0)
        ppsp_e6c = e6c.get("ppsp_db", 0.0)
        
        if snr_e1b < min_snr_db or snr_e6c < min_snr_db:
            skipped_count += 1
            authentication_results.append({
                "prn": prn,
                "authenticated": None,
                "status": f"SKIPPED: Low SNR (E1B={snr_e1b:.1f}dB, E6C={snr_e6c:.1f}dB < {min_snr_db}dB)",
                "snr_e1b_db": snr_e1b,
                "snr_e6c_db": snr_e6c,
            })
            continue
        
        # Get code delays (peak indices)
        # Use same-period matched E1B peak when available (code-Doppler
        # compensation).  See Module_SignalCorrelation.py for details.
        delay_e1b = e1b.get("peak_index_interp_matched",
                           e1b.get("peak_index_interp", e1b.get("peak_index", 0)))
        delay_e6c = e6c.get("peak_index_interp", e6c.get("peak_index", 0))
        
        # Perform authentication
        auth_result = authenticate_measurement(
            code_delay_e1b_samples=delay_e1b,
            code_delay_e6c_samples=delay_e6c,
            sample_rate_hz=sample_rate_hz,
            gamma_auth_m=gamma_auth,
            sigma_auth_m=sigma_auth,
            kappa=kappa,
            delta_e1_e6_samples=delta_e1_e6_samples,
            snr_e1b_db=snr_e1b,
            snr_e6c_db=snr_e6c,
            ppsp_e1b_db=ppsp_e1b,
            ppsp_e6c_db=ppsp_e6c,
            prn=prn,
        )
        
        if auth_result.authenticated:
            authenticated_count += 1
        else:
            failed_count += 1
        
        authentication_results.append(auth_result.to_dict())
    
    # Calculate overall position authentication (equation 22)
    # Position is authenticated if all measurements are authenticated
    all_authenticated = (authenticated_count > 0 and failed_count == 0)
    
    # Convert thresholds to samples for display
    gamma_auth_samples = gamma_auth / SPEED_OF_LIGHT * sample_rate_hz
    sigma_auth_samples = sigma_auth / SPEED_OF_LIGHT * sample_rate_hz
    
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    
    result = {
        "success": True,
        "timestamp": timestamp,
        
        # Overall authentication status
        "position_authenticated": all_authenticated,
        "authenticated_count": authenticated_count,
        "failed_count": failed_count,
        "skipped_count": skipped_count,
        "total_prns": len(all_prns),
        
        # Per-PRN results
        "prn_results": authentication_results,
        
        # Receiver hardware bias info (HWB_rx from Galileo SAS paper)
        "receiver_offset": receiver_offset_info,
        
        # Configuration used (samples as primary, meters for reference)
        "config": {
            "kappa": kappa,
            "confidence_level_percent": get_confidence_percentage(kappa),
            "gamma_auth_samples": gamma_auth_samples,
            "sigma_auth_samples": sigma_auth_samples,
            "gamma_auth_m": gamma_auth,
            "sigma_auth_m": sigma_auth,
            "sample_rate_hz": sample_rate_hz,
            "min_snr_db": min_snr_db,
            "min_ppsp_db": min_ppsp_db,
        },
        
        # Error model used
        "error_model": {
            "sigma_hwbrx_m": sigma_hwbrx,
            "sigma_bgd_m": sigma_bgd,
            "sigma_iono_e1_m": sigma_iono,
            "sigma_mp_e1_m": sigma_mp_e1,
            "sigma_mp_e6_m": sigma_mp_e6,
            "sigma_noise_e1_m": sigma_noise_e1,
            "sigma_noise_e6_m": sigma_noise_e6,
        },
        
        # Summary message
        "summary": _build_summary(all_authenticated, authenticated_count, failed_count, skipped_count, kappa),
    }
    
    # Optionally save results
    if coerce_bool(config.get("save_results", True), True):
        from utils.execution_context import get_execution_context
        ctx = get_execution_context()

        output_dir = config.get("output_directory")
        if output_dir:
            output_path = Path(output_dir)
        elif ctx.get_execution_id():
            output_path = ctx.get_execution_results_dir("authentication")
        else:
            output_path = get_results_subdir("authentication")
        
        output_path.mkdir(parents=True, exist_ok=True)
        result_file = output_path / f"authentication_{timestamp}.json"
        save_json_file(result_file, result)
        result["output_file"] = str(result_file)
        
        # Generate visualization plots
        if coerce_bool(config.get("generate_plots", True), True):
            try:
                # Save plots in the execution's plots/ directory
                if ctx.get_execution_id():
                    plots_path = ctx.get_execution_results_dir("plots")
                else:
                    plots_path = output_path
                plot_result = generate_authentication_plots(
                    auth_results=authentication_results,
                    gamma_auth_m=gamma_auth,
                    sigma_auth_m=sigma_auth,
                    kappa=kappa,
                    output_path=plots_path,
                    correlation_profiles=correlation_results.get("correlation_profiles"),
                    sample_rate_hz=sample_rate_hz,
                )
                if plot_result:
                    main_plot, comparison_plot = plot_result
                    if main_plot:
                        result["plot_file"] = main_plot
                    if comparison_plot:
                        result["comparison_plot_file"] = comparison_plot
            except Exception as e:
                import traceback
                console_log("SatelliteAuthenticator", f"Warning: Could not generate plots: {e}\n{traceback.format_exc()}")
    
    return result


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: correlation_results
    produces: authentication_results
    artifacts: plot (auto_open)
    """
    inputs = dict(inputs or {})
    raw = _run_pipeline_legacy(
        config=config,
        correlation_results=inputs.get("correlation_results"),
    )
    plot_path = raw.get("plot_file") if isinstance(raw, dict) else None
    return {
        "outputs": {
            "authentication_results": raw,
        },
        "artifacts": {"plot": plot_path} if plot_path else {},
        "summary": {
            "position_authenticated": raw.get("position_authenticated") if isinstance(raw, dict) else None,
            "authenticated_count": raw.get("authenticated_count") if isinstance(raw, dict) else 0,
            "failed_count": raw.get("failed_count") if isinstance(raw, dict) else 0,
            "skipped_count": raw.get("skipped_count") if isinstance(raw, dict) else 0,
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"


def _build_summary(
    all_authenticated: bool,
    authenticated_count: int,
    failed_count: int,
    skipped_count: int,
    kappa: float
) -> str:
    """Build a human-readable summary message."""
    confidence = get_confidence_percentage(kappa)
    
    if all_authenticated:
        return (
            f"POSITION AUTHENTICATED ({confidence:.1f}% confidence)\n"
            f"   All {authenticated_count} PRN(s) verified successfully"
        )
    elif failed_count > 0:
        return (
            f"POSITION NOT AUTHENTICATED\n"
            f"   {failed_count} PRN(s) failed verification\n"
            f"   {authenticated_count} authenticated, {skipped_count} skipped"
        )
    elif authenticated_count == 0:
        return (
            f"UNABLE TO AUTHENTICATE\n"
            f"   No valid E1B+E6C pairs found\n"
            f"   {skipped_count} PRN(s) skipped due to missing data or low SNR"
        )
    else:
        return (
            f"PARTIAL AUTHENTICATION\n"
            f"   {authenticated_count} authenticated, {skipped_count} skipped"
        )


def run_pipeline_from_json(payload: Union[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Execute the authentication pipeline from a JSON-compatible payload."""
    if isinstance(payload, str):
        try:
            config_map = json.loads(payload.strip() or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("Invalid JSON payload for authentication pipeline") from exc
    elif isinstance(payload, Mapping):
        config_map = payload
    else:
        raise TypeError("Authentication payload must be a mapping or JSON string")

    return run_pipeline(config_map)


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function for direct authentication check
# ─────────────────────────────────────────────────────────────────────────────

def quick_authenticate(
    correlation_results: Dict[str, Any],
    kappa: float = 3.0,
) -> Tuple[bool, str]:
    """
    Quick authentication check with default parameters.
    
    Args:
        correlation_results: Results from SignalCorrelation.run_pipeline()
        kappa: Confidence factor (default 3.0 for 99.7%)
        
    Returns:
        Tuple of (is_authenticated, summary_message)
    """
    result = run_pipeline({
        "correlation_results": correlation_results,
        "kappa": kappa,
        "save_results": False,
    })
    
    return result["position_authenticated"], result["summary"]


# ─────────────────────────────────────────────────────────────────────────────
# Exports
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Schema/Config
    "load_schema",
    "load_config",
    "save_config",
    "get_defaults",
    
    # Pipeline
    "run_pipeline",
    "run_pipeline_from_json",
    
    # Core functions
    "calculate_sigma_auth",
    "calculate_gamma_auth",
    "authenticate_measurement",
    "quick_authenticate",
    
    # Utilities
    "samples_to_meters",
    "meters_to_samples",
    "get_confidence_percentage",
    
    # Visualization
    "generate_authentication_plots",
    
    # Physical constants
    "F1_HZ",
    "F6_HZ",
    "E1_CHIP_RATE_HZ",
    "E6_CHIP_RATE_HZ",
    "E1_CHIP_METERS",
    "E6_CHIP_METERS",
    "E1_CODE_LENGTH_CHIPS",
    "E6B_CODE_LENGTH_CHIPS",
    
    # Dataclass
    "AuthenticationResult",
]
