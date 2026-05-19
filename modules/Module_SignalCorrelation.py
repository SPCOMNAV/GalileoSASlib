"""
Module_SignalCorrelation - Galileo E1/E6 Signal Correlation Pipeline

This module provides signal correlation for Galileo E1B, E1C, E6B, and E6C signals.
It uses modular subcomponents from the modules.correlation package.

Main entry point: run_pipeline()
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

from utils.utils import (
    load_json_file,
    save_json_file,
    console_log,
    BASE_DIR,
    SCHEMA_DIR,
    CONFIG_DIR,
    get_results_subdir,
)

# Import from correlation submodules
from .correlation import (
    # Constants
    E6_CHIP_RATE_HZ, E6_CODE_LENGTH_CHIPS, E6_CARRIER_HZ,
    E1_CHIP_RATE_HZ, E1_CODE_LENGTH_CHIPS, E1_CARRIER_HZ,
    # Loading
    load_samples_sc16_window,
    load_chips_for_band,
    load_e6b_chips, load_e6c_chips, load_e1b_chips,
    load_ecs_from_hex, load_ecs_from_results,
    # Signal processing
    compute_correlation,
    correlate_full_signal,
    search_doppler_range, search_doppler_fine,
    perform_acquisition, AcquisitionResult,
    # Metrics
    calculate_ppsp_db, interpolate_caf_peak_1d,
    estimate_cn0_gonzalo_salcedo,
    # Peak detection
    find_first_peak,
    # Visualization
    save_correlation_plot,
)


############################################################ Schema / Config loaders #################################

def load_schema() -> Dict[str, Any]:
    """Load the block schema from JSON."""
    return load_json_file(SCHEMA_DIR / "SignalCorrelation_schema.json")


def load_config() -> Dict[str, Any]:
    """Load current config values."""
    return load_json_file(CONFIG_DIR / "SignalCorrelation.json")


def save_config(config: Dict[str, Any]) -> None:
    """Persist config values."""
    save_json_file(CONFIG_DIR / "SignalCorrelation.json", config)


############################################################# Helper functions ########################################

def _debug(msg: str) -> None:
    console_log("SignalCorrelation", msg)


def _check_numpy() -> None:
    """Verify numpy is available."""
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for signal correlation but is not installed")


_save_correlation_plot = save_correlation_plot


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline
# ─────────────────────────────────────────────────────────────────────────────

def _run_pipeline_legacy(
    config: Optional[Mapping[str, Any]] = None,
    *,
    sample_rate_hz: Optional[float] = None,
    ecs_hex: Optional[str] = None,
    ecs_path: Optional[str] = None,
    snapshot_path_e1: Optional[str] = None,
    snapshot_path_e6: Optional[str] = None,
    auxiliary_mode: Optional[str] = None,
    prn_list: Optional[str] = None,
    prn_encrypted_list: Optional[str] = None,
    # ACAS code-phase assist (only applied to the E6C-with-ECS acquisition).
    # When any of these is None / 0, the search falls back to the full window.
    snapshot_gst_sec: Optional[float] = None,
    gst_recs_start_s: Optional[float] = None,
    total_uncertainty_ms: Optional[float] = None,
    **kwargs,
) -> Dict[str, Any]:
    """
    Run the signal correlation pipeline.
    
    Automatically detects multi-band mode if iq_path_e1 or prn_list is present.
    
    Args:
        config: Optional configuration mapping to override JSON config.
        sample_rate_hz: Sample rate in Hz (from SASpipeline).
        ecs_hex: ECS hex string (from SASpipeline).
        ecs_path: ECS file path (from SASpipeline).
        snapshot_path_e1: E1 snapshot path (from SASpipeline).
        snapshot_path_e6: E6 snapshot path (from SASpipeline).
        prn_list: Comma-separated PRN list (from ConfigInit.svid_to_search).
        prn_encrypted_list: Comma-separated list of PRNs with ECS (from ConfigInit.svid_encrypted).
    
    Returns:
        Dictionary with correlation results per PRN
    """
    _check_numpy()
    
    # Load config from own JSON, then apply overrides
    base_config = load_config()
    if config:
        base_config.update(config)
    data = base_config
    
    # Determine mode: E6 only, or E1+E6 if auxiliary_mode is E1B/E1C AND E1 snapshot is provided
    aux_mode = auxiliary_mode or data.get("auxiliary_mode", "off")
    e1_enabled = aux_mode in ("E1B", "E1C")
    has_e1 = e1_enabled and bool(snapshot_path_e1 or data.get("iq_path_e1"))
    
    if has_e1:
        _debug(f"Using multi-band mode (E1+E6), auxiliary_mode={aux_mode}")
    else:
        _debug(f"Using single-band mode (E6 only), auxiliary_mode={aux_mode}")
    
    # Get IQ file path
    iq_file_path_raw = snapshot_path_e6 or data.get("iq_file_path", "")
    if not iq_file_path_raw:
        return {
            "success": False,
            "error": "No IQ file path specified",
            "results": [],
        }
    
    iq_file_path = Path(iq_file_path_raw)
    if not iq_file_path.is_absolute():
        iq_file_path = (BASE_DIR / iq_file_path).resolve()
    
    if not iq_file_path.exists():
        return {
            "success": False,
            "error": f"IQ file not found: {iq_file_path}",
            "results": [],
        }
    
    # Parse parameters - use passed sample_rate_hz if provided, otherwise from config
    fs_hz = float(sample_rate_hz) if sample_rate_hz else float(data.get("sample_rate_hz", 20e6))
    _debug(f"Using sample rate: {fs_hz/1e6:.2f} MHz")
    
    start_sample = int(data.get("start_sample", 0))
    num_samples = int(data.get("num_samples", -1))
    
    # Use prn_list from parameter (from ConfigInit / auto-visible) if provided,
    # otherwise from own JSON config.
    # "auto" or empty string means the pipeline already resolved it to the
    # almanac-visible PRN list; if it somehow arrives here unresolved, fall
    # back to config or a sensible default.
    prn_list_raw = prn_list if prn_list else data.get("prn_list", "14")
    if isinstance(prn_list_raw, str):
        cleaned = prn_list_raw.strip()
        if cleaned.lower() == "auto" or cleaned == "":
            # Unresolved auto — should not happen if pipeline ran Almanac
            _debug("prn_list='auto' reached SignalCorrelation unresolved; using all Galileo PRNs")
            prns = list(range(1, 37))
        else:
            prns = [int(p.strip()) for p in cleaned.split(",") if p.strip().isdigit()]
    elif isinstance(prn_list_raw, list):
        prns = [int(p) for p in prn_list_raw]
    else:
        prns = [14]
    _debug(f"PRN list ({len(prns)} sats): {prns}")
    
    # Parse PRNs with ECS (only these will have E6C correlation)
    prn_enc_raw = prn_encrypted_list if prn_encrypted_list else data.get("prn_encrypted_list", "")
    if isinstance(prn_enc_raw, str) and prn_enc_raw.strip():
        prns_with_ecs = [int(p.strip()) for p in prn_enc_raw.split(",") if p.strip().isdigit()]
    elif isinstance(prn_enc_raw, list):
        prns_with_ecs = [int(p) for p in prn_enc_raw]
    else:
        prns_with_ecs = []
    _debug(f"PRNs with ECS (E6C enabled): {prns_with_ecs}")
    
    # Integration parameters (separate for E6 and auxiliary bands)
    # Ni = non-coherent blocks, Nc = coherent periods per block
    # Total integration time: Tint = Nc × Ni × Tcode
    ni_periods_e6 = int(data.get("ni_periods_e6", data.get("ni_periods", 20)))  # Fallback to old param
    ni_periods_aux = int(data.get("ni_periods_aux", max(1, ni_periods_e6 // 4)))  # Default: E6/4 for similar Tint
    nc_coherent_e6 = int(data.get("nc_coherent_e6", 1))   # Nc for E6 (1ms code period)
    nc_coherent_aux = int(data.get("nc_coherent_aux", 1))  # Nc for E1 (4ms code period)
    _debug(f"Using Nc_E6={nc_coherent_e6}, Ni_E6={ni_periods_e6} (Tint={nc_coherent_e6*ni_periods_e6}ms), "
           f"Nc_Aux={nc_coherent_aux}, Ni_Aux={ni_periods_aux} (Tint={nc_coherent_aux*ni_periods_aux*4}ms)")

    doppler_min = float(data.get("doppler_min_hz", -6500.0))
    doppler_max = float(data.get("doppler_max_hz", 6500.0))
    doppler_step = float(data.get("doppler_step_hz", 100.0))

    # Fine Doppler search parameters
    fine_doppler_search = bool(data.get("fine_doppler_search", True))
    fine_doppler_range_hz = float(data.get("fine_doppler_range_hz", 100.0))

    correct_code_doppler = bool(data.get("correct_code_doppler", True))
    carrier_hz = float(data.get("carrier_frequency_hz", E6_CARRIER_HZ))
    interpolation_mode = str(data.get("interpolation_mode", "quadratic-polynomial-3-points"))
    ppsp_exclusion_chips = int(data.get("ppsp_exclusion_chips", 50))

    # CAF / PDI parameters (Terris v0.2)
    pdi_method = str(data.get("pdi_method", "NPDI"))
    exp_n_coh = float(data.get("exp_n_coh", 2.0))
    save_all_cafs = bool(data.get("save_all_cafs", False))
    _debug(f"CAF params: pdi_method={pdi_method}, exp_n_coh={exp_n_coh}, save_all_cafs={save_all_cafs}")

    # Build coarse Doppler search grid
    doppler_bins_coarse = np.arange(doppler_min, doppler_max + doppler_step / 2, doppler_step)
    
    # Load IQ samples
    _debug(f"Loading IQ samples from: {iq_file_path}")
    try:
        iq_full = load_samples_sc16_window(str(iq_file_path), start_sample, num_samples)
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to load IQ samples: {e}",
            "results": [],
        }
    
    if iq_full.size == 0:
        return {
            "success": False,
            "error": "No IQ samples loaded",
            "results": [],
        }
    
    _debug(f"Loaded {iq_full.size} samples")
    
    # Load E1 IQ samples if available
    iq_full_e1 = None
    fs_hz_e1 = fs_hz  # Assume same sample rate, can be made configurable
    if has_e1:
        e1_path_raw = snapshot_path_e1 or data.get("iq_path_e1", "")
        if e1_path_raw:
            e1_path = Path(e1_path_raw)
            if not e1_path.is_absolute():
                e1_path = (BASE_DIR / e1_path).resolve()
            
            if e1_path.exists():
                try:
                    iq_full_e1 = load_samples_sc16_window(str(e1_path), start_sample, num_samples)
                    _debug(f"Loaded E1 IQ: {iq_full_e1.size} samples from {e1_path}")
                except Exception as e:
                    _debug(f"Failed to load E1 IQ samples: {e}")
            else:
                _debug(f"E1 IQ file not found: {e1_path}")
    
    # Try to load ECS for E6C correlation
    # Priority: 1) ecs_hex parameter (from pipeline), 2) file from results
    ecs_chips = None
    ecs_file = None
    
    if ecs_hex:
        # Use ECS passed from pipeline (from RECSDecryption block)
        try:
            ecs_chips = load_ecs_from_hex(ecs_hex)
            ecs_file = ecs_path or "from_pipeline"
            _debug(f"Loaded ECS from pipeline parameter: {len(ecs_hex)//2} bytes")
        except Exception as e:
            _debug(f"Failed to load ECS from pipeline parameter: {e}")
    
    if ecs_chips is None:
        # Fallback: try to load from results folder
        ecs_data = load_ecs_from_results()
        if ecs_data:
            ecs_hex_file, ecs_file = ecs_data
            try:
                ecs_chips = load_ecs_from_hex(ecs_hex_file)
                _debug(f"Loaded ECS from file: {ecs_file}")
            except Exception as e:
                _debug(f"Failed to load ECS from file: {e}")
    
    if ecs_chips is None:
        _debug("No ECS available - E6C will use public code for all PRNs")
    
    # Process each PRN
    results_e1 = []   # Will be populated when E1 snapshots are provided
    results_e6b = []
    results_e6c = []
    correlation_profiles = {}  # Store power profiles for plotting
    
    for prn in prns:
        if prn not in prns:
            raise RuntimeError(f"Intentando procesar PRN {prn} que no está en la lista de entrada: {prns}")
        _debug(f"Procesando PRN {prn} (E6B/E6C PRN {prn})")
        
        # ─────────────────────────────────────────────────────────────────
        # E6B CORRELATION  (perform_acquisition → CAF 2D)
        # ─────────────────────────────────────────────────────────────────
        try:
            e6b_chips = load_e6b_chips(prn)

            # --- Coarse acquisition (full Doppler grid) ---
            acq_e6b = perform_acquisition(
                signal_rx=iq_full,
                replica_chips=e6b_chips,
                fs_hz=fs_hz,
                doppler_bins=doppler_bins_coarse,
                chip_rate_hz=E6_CHIP_RATE_HZ,
                carrier_freq_hz=E6_CARRIER_HZ,
                nc_coherent=nc_coherent_e6,
                n_pdi=ni_periods_e6,
                exp_n_coh=exp_n_coh,
                pdi_method=pdi_method,
                correct_code_doppler=False,
                use_boc=False,
                save_all_cafs=save_all_cafs,
            )

            # Peak of caf_pdi → coarse (code-phase, Doppler)
            caf_pdi_abs = np.abs(acq_e6b.caf_pdi) if np.iscomplexobj(acq_e6b.caf_pdi) else acq_e6b.caf_pdi
            peak_2d = np.unravel_index(np.argmax(caf_pdi_abs), caf_pdi_abs.shape)
            coarse_doppler_hz = float(acq_e6b.doppler_bins[peak_2d[1]])
            _debug(f"  E6B PRN {prn}: Coarse peak cp={peak_2d[0]}, Fd={coarse_doppler_hz:.0f} Hz")

            # --- Fine Doppler search ---
            best_doppler_hz = coarse_doppler_hz
            best_acq_e6b = acq_e6b
            if fine_doppler_search:
                fine_bins = np.arange(
                    coarse_doppler_hz - fine_doppler_range_hz,
                    coarse_doppler_hz + fine_doppler_range_hz + 0.5,
                    1.0)
                acq_e6b_fine = perform_acquisition(
                    signal_rx=iq_full,
                    replica_chips=e6b_chips,
                    fs_hz=fs_hz,
                    doppler_bins=fine_bins,
                    chip_rate_hz=E6_CHIP_RATE_HZ,
                    carrier_freq_hz=E6_CARRIER_HZ,
                    nc_coherent=nc_coherent_e6,
                    n_pdi=ni_periods_e6,
                    exp_n_coh=exp_n_coh,
                    pdi_method=pdi_method,
                    correct_code_doppler=correct_code_doppler,
                    use_boc=False,
                    save_all_cafs=False,
                )
                pdi_fine = np.abs(acq_e6b_fine.caf_pdi) if np.iscomplexobj(acq_e6b_fine.caf_pdi) else acq_e6b_fine.caf_pdi
                peak_fine = np.unravel_index(np.argmax(pdi_fine), pdi_fine.shape)
                best_doppler_hz = float(acq_e6b_fine.doppler_bins[peak_fine[1]])
                best_acq_e6b = acq_e6b_fine
                _debug(f"  E6B PRN {prn}: Fine Doppler={best_doppler_hz:.1f} Hz")

            # Extract 1-D power profile at best Doppler column
            pdi_final = np.abs(best_acq_e6b.caf_pdi) if np.iscomplexobj(best_acq_e6b.caf_pdi) else best_acq_e6b.caf_pdi
            best_fd_idx = int(np.argmin(np.abs(best_acq_e6b.doppler_bins - best_doppler_hz)))
            power_e6b = pdi_final[:, best_fd_idx].astype(np.float64)

            # Complex CAF column for C/N0 estimation
            complex_caf_e6b = best_acq_e6b.caf_coh[:, best_fd_idx]

            # Peak analysis E6B - use first peak (LOS)
            max_idx_e6b = int(np.argmax(power_e6b))
            first_peak_idx_e6b = find_first_peak(power_e6b, threshold_ratio=0.7)

            if first_peak_idx_e6b < max_idx_e6b:
                peak_idx_e6b = first_peak_idx_e6b
                _debug(f"  E6B PRN {prn}: Using first peak at {first_peak_idx_e6b} (max at {max_idx_e6b})")
            else:
                peak_idx_e6b = max_idx_e6b

            peak_power_e6b = float(power_e6b[peak_idx_e6b])
            caf_peak_complex_e6b = complex_caf_e6b[peak_idx_e6b]

            idx_interp_e6b, peak_interp_e6b = interpolate_caf_peak_1d(power_e6b, mode=interpolation_mode, peak_idx=peak_idx_e6b)

            # PPSP calculation (within one code period)
            exclusion_width = int(fs_hz / E6_CHIP_RATE_HZ * ppsp_exclusion_chips)
            samples_per_code_e6 = int(fs_hz / E6_CHIP_RATE_HZ * E6_CODE_LENGTH_CHIPS)
            ppsp_db_e6b = calculate_ppsp_db(power_e6b, peak_idx_e6b, exclusion_width, samples_per_code=samples_per_code_e6)

            # SNR calculation
            noise_power_e6b = float(np.median(power_e6b))
            snr_like_e6b = peak_power_e6b / (noise_power_e6b + 1e-12)
            snr_output_db_e6b = 10.0 * math.log10(snr_like_e6b) if snr_like_e6b > 0 else float("-inf")

            # C/N0 estimation using Gonzalo-Salcedo method
            nscode_e6b = int(fs_hz / E6_CHIP_RATE_HZ * E6_CODE_LENGTH_CHIPS)
            cn0_dbhz_e6b = estimate_cn0_gonzalo_salcedo(
                signal_rx=iq_full,
                caf_peak_complex=caf_peak_complex_e6b,
                nscode=nscode_e6b,
                nc=nc_coherent_e6, ni=ni_periods_e6, fs_hz=fs_hz,
                t_int_ref_ms=1.0
            )

            # Store correlation profile for plotting
            correlation_profiles[f"E6B_PRN{prn}"] = {
                "power": power_e6b,
                "fs_hz": fs_hz,
                "peak_idx": peak_idx_e6b,
            }

            results_e6b.append({
                "prn": prn,
                "signal": "E6B",
                "best_doppler_hz": best_doppler_hz,
                "peak_index": peak_idx_e6b,
                "peak_index_interp": idx_interp_e6b,
                "peak_power": peak_power_e6b,
                "peak_power_interp": peak_interp_e6b,
                "ppsp_db": ppsp_db_e6b,
                "snr_like": snr_like_e6b,
                "snr_output_db": snr_output_db_e6b,
                "cn0_dbhz": cn0_dbhz_e6b,
            })

            _debug(f"  E6B PRN {prn}: PPSP={ppsp_db_e6b:.2f} dB, SNR={snr_output_db_e6b:.2f} dB, C/N0={cn0_dbhz_e6b:.2f} dB-Hz, Doppler={best_doppler_hz:.1f} Hz")

        except Exception as e:
            _debug(f"Error processing E6B PRN {prn}: {e}")
            results_e6b.append({
                "prn": prn,
                "signal": "E6B",
                "error": str(e),
            })
        
        # ─────────────────────────────────────────────────────────────────
        # E6C CORRELATION  (perform_acquisition, Doppler from E6B)
        # ─────────────────────────────────────────────────────────────────
        try:
            _debug(f"  E6C PRN {prn}: prn in prns_with_ecs? {prn in prns_with_ecs}, ecs_chips loaded? {ecs_chips is not None}")

            # Determine which code to use for E6C correlation
            if prn in prns_with_ecs and ecs_chips is not None:
                e6c_code = ecs_chips
                e6c_mode = "encrypted (ECS)"
            else:
                if prn in prns_with_ecs and ecs_chips is None:
                    _debug(f"  WARNING: PRN {prn} is in svid_encrypted but ECS not loaded! Using public code.")
                e6c_code = load_e6c_chips(prn)
                e6c_mode = "public"

            _debug(f"  E6C PRN {prn}: using {e6c_mode} code")

            # Use Doppler from E6B — single Doppler bin
            e6b_result = results_e6b[-1] if results_e6b else {}
            doppler_e6c = e6b_result.get("best_doppler_hz", 0.0)

            # ACAS code-phase assist (E6C only): predicted position is
            # relative to the loaded IQ, so subtract start_sample.
            cp_est_e6c = 0
            ns_acq_e6c = 0
            if (
                e6c_mode == "encrypted (ECS)"
                and snapshot_gst_sec is not None
                and gst_recs_start_s is not None
                and total_uncertainty_ms
                and float(total_uncertainty_ms) > 0
            ):
                delta_t = float(gst_recs_start_s) - float(snapshot_gst_sec)
                abs_sample = int(delta_t * fs_hz)
                predicted = abs_sample - int(start_sample)
                if 0 <= predicted < iq_full.size:
                    cp_est_e6c = predicted
                    ns_acq_e6c = int(2.0 * float(total_uncertainty_ms) * 1e-3 * fs_hz)
                    _debug(
                        f"  E6C PRN {prn}: ACAS assist Δt={delta_t:.6f}s "
                        f"abs_sample={abs_sample} start_sample={start_sample} → "
                        f"cp_est={cp_est_e6c}, ns_acq={ns_acq_e6c}"
                    )
                else:
                    _debug(
                        f"  E6C PRN {prn}: ACAS prediction {predicted} outside loaded IQ "
                        f"[0, {iq_full.size}); falling back to full-window search"
                    )

            acq_e6c = perform_acquisition(
                signal_rx=iq_full,
                replica_chips=e6c_code,
                fs_hz=fs_hz,
                doppler_bins=np.array([doppler_e6c]),
                chip_rate_hz=E6_CHIP_RATE_HZ,
                carrier_freq_hz=E6_CARRIER_HZ,
                nc_coherent=nc_coherent_e6,
                n_pdi=ni_periods_e6,
                exp_n_coh=exp_n_coh,
                cp_est_ini=cp_est_e6c,
                ns_acq=ns_acq_e6c,
                pdi_method=pdi_method,
                correct_code_doppler=correct_code_doppler,
                use_boc=False,
                save_all_cafs=save_all_cafs,
            )

            # Extract 1-D power profile (single freq bin → column 0)
            pdi_e6c = np.abs(acq_e6c.caf_pdi) if np.iscomplexobj(acq_e6c.caf_pdi) else acq_e6c.caf_pdi
            power_e6c = pdi_e6c[:, 0].astype(np.float64)
            complex_caf_e6c = acq_e6c.caf_coh[:, 0]

            # Peak analysis E6C
            max_idx_e6c = int(np.argmax(power_e6c))

            if e6c_mode == "encrypted (ECS)":
                peak_idx_e6c = max_idx_e6c
            else:
                first_peak_idx_e6c = find_first_peak(power_e6c, threshold_ratio=0.7)
                if first_peak_idx_e6c < max_idx_e6c:
                    peak_idx_e6c = first_peak_idx_e6c
                    _debug(f"  E6C PRN {prn}: Using first peak at {first_peak_idx_e6c} (max at {max_idx_e6c})")
                else:
                    peak_idx_e6c = max_idx_e6c

            peak_power_e6c = float(power_e6c[peak_idx_e6c])
            caf_peak_complex_e6c = complex_caf_e6c[peak_idx_e6c]

            idx_interp_e6c, peak_interp_e6c = interpolate_caf_peak_1d(power_e6c, mode=interpolation_mode, peak_idx=peak_idx_e6c)

            # PPSP
            exclusion_width = int(fs_hz / E6_CHIP_RATE_HZ * ppsp_exclusion_chips)
            samples_per_code_e6 = int(fs_hz / E6_CHIP_RATE_HZ * E6_CODE_LENGTH_CHIPS)
            if e6c_mode == "encrypted (ECS)":
                ppsp_db_e6c = calculate_ppsp_db(power_e6c, peak_idx_e6c, exclusion_width, samples_per_code=None)
            else:
                ppsp_db_e6c = calculate_ppsp_db(power_e6c, peak_idx_e6c, exclusion_width, samples_per_code=samples_per_code_e6)

            # SNR
            noise_power_e6c = float(np.median(power_e6c))
            snr_like_e6c = peak_power_e6c / (noise_power_e6c + 1e-12)
            snr_output_db_e6c = 10.0 * math.log10(snr_like_e6c) if snr_like_e6c > 0 else float("-inf")

            # C/N0
            nscode_e6c = int(fs_hz / E6_CHIP_RATE_HZ * len(e6c_code))
            cn0_dbhz_e6c = estimate_cn0_gonzalo_salcedo(
                signal_rx=iq_full,
                caf_peak_complex=caf_peak_complex_e6c,
                nscode=nscode_e6c,
                nc=nc_coherent_e6, ni=ni_periods_e6, fs_hz=fs_hz,
                t_int_ref_ms=1.0
            )

            correlation_profiles[f"E6C_PRN{prn}"] = {
                "power": power_e6c,
                "fs_hz": fs_hz,
                "peak_idx": peak_idx_e6c,
                "mode": e6c_mode,
            }

            results_e6c.append({
                "prn": prn,
                "signal": "E6C",
                "mode": e6c_mode,
                "best_doppler_hz": doppler_e6c,
                "peak_index": peak_idx_e6c,
                "peak_index_interp": idx_interp_e6c,
                "peak_power": peak_power_e6c,
                "peak_power_interp": peak_interp_e6c,
                "ppsp_db": ppsp_db_e6c,
                "snr_like": snr_like_e6c,
                "snr_output_db": snr_output_db_e6c,
                "cn0_dbhz": cn0_dbhz_e6c,
            })

            _debug(f"  E6C PRN {prn} ({e6c_mode}): PPSP={ppsp_db_e6c:.2f} dB, SNR={snr_output_db_e6c:.2f} dB, C/N0={cn0_dbhz_e6c:.2f} dB-Hz")

        except Exception as e:
            _debug(f"Error processing E6C PRN {prn}: {e}")
            results_e6c.append({
                "prn": prn,
                "signal": "E6C",
                "error": str(e),
            })
    
    # ─────────────────────────────────────────────────────────────────────────
    # E1B CORRELATION  (perform_acquisition + BOC(1,1), auxiliary signal)
    # ─────────────────────────────────────────────────────────────────────────
    if iq_full_e1 is not None and iq_full_e1.size > 0:
        _debug("Processing E1B correlation...")

        for prn in prns:
            try:
                e1b_chips = load_e1b_chips(prn)

                # --- Coarse acquisition E1B (full Doppler grid, BOC) ---
                acq_e1b = perform_acquisition(
                    signal_rx=iq_full_e1,
                    replica_chips=e1b_chips,
                    fs_hz=fs_hz_e1,
                    doppler_bins=doppler_bins_coarse,
                    chip_rate_hz=E1_CHIP_RATE_HZ,
                    carrier_freq_hz=E1_CARRIER_HZ,
                    nc_coherent=nc_coherent_aux,
                    n_pdi=ni_periods_aux,
                    exp_n_coh=exp_n_coh,
                    pdi_method=pdi_method,
                    correct_code_doppler=False,
                    use_boc=True,
                    save_all_cafs=save_all_cafs,
                )

                pdi_e1b_abs = np.abs(acq_e1b.caf_pdi) if np.iscomplexobj(acq_e1b.caf_pdi) else acq_e1b.caf_pdi
                peak_2d_e1b = np.unravel_index(np.argmax(pdi_e1b_abs), pdi_e1b_abs.shape)
                coarse_doppler_e1b = float(acq_e1b.doppler_bins[peak_2d_e1b[1]])
                _debug(f"  E1B PRN {prn}: Coarse peak cp={peak_2d_e1b[0]}, Fd={coarse_doppler_e1b:.0f} Hz")

                # --- Fine Doppler search for E1B ---
                best_doppler_e1b = coarse_doppler_e1b
                best_acq_e1b = acq_e1b
                if fine_doppler_search:
                    fine_bins_e1 = np.arange(
                        coarse_doppler_e1b - fine_doppler_range_hz,
                        coarse_doppler_e1b + fine_doppler_range_hz + 0.5,
                        1.0)
                    acq_e1b_fine = perform_acquisition(
                        signal_rx=iq_full_e1,
                        replica_chips=e1b_chips,
                        fs_hz=fs_hz_e1,
                        doppler_bins=fine_bins_e1,
                        chip_rate_hz=E1_CHIP_RATE_HZ,
                        carrier_freq_hz=E1_CARRIER_HZ,
                        nc_coherent=nc_coherent_aux,
                        n_pdi=ni_periods_aux,
                        exp_n_coh=exp_n_coh,
                        pdi_method=pdi_method,
                        correct_code_doppler=correct_code_doppler,
                        use_boc=True,
                        save_all_cafs=False,
                    )
                    pdi_fine_e1 = np.abs(acq_e1b_fine.caf_pdi) if np.iscomplexobj(acq_e1b_fine.caf_pdi) else acq_e1b_fine.caf_pdi
                    peak_fine_e1 = np.unravel_index(np.argmax(pdi_fine_e1), pdi_fine_e1.shape)
                    best_doppler_e1b = float(acq_e1b_fine.doppler_bins[peak_fine_e1[1]])
                    best_acq_e1b = acq_e1b_fine
                    _debug(f"  E1B PRN {prn}: Fine Doppler={best_doppler_e1b:.1f} Hz")

                # Extract 1-D power profile at best Doppler column
                pdi_e1b_final = np.abs(best_acq_e1b.caf_pdi) if np.iscomplexobj(best_acq_e1b.caf_pdi) else best_acq_e1b.caf_pdi
                best_fd_idx_e1 = int(np.argmin(np.abs(best_acq_e1b.doppler_bins - best_doppler_e1b)))
                power_e1b = pdi_e1b_final[:, best_fd_idx_e1].astype(np.float64)
                complex_caf_e1b = best_acq_e1b.caf_coh[:, best_fd_idx_e1]

                # Peak analysis E1B — use max peak for best code-phase accuracy
                max_idx_e1b = int(np.argmax(power_e1b))
                peak_idx_e1b = max_idx_e1b
                samples_per_code_e1 = int(fs_hz_e1 / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS)
                _debug(f"  E1B PRN {prn}: Using max peak at {max_idx_e1b} (phase mod {samples_per_code_e1}={max_idx_e1b % samples_per_code_e1})")

                # Also store first_peak for multipath / LOS analysis
                first_peak_idx_e1b = find_first_peak(power_e1b, threshold_ratio=0.7)
                if first_peak_idx_e1b < max_idx_e1b:
                    _debug(f"  E1B PRN {prn}: First peak at {first_peak_idx_e1b} (phase={first_peak_idx_e1b % samples_per_code_e1})")

                peak_power_e1b = float(power_e1b[peak_idx_e1b])
                caf_peak_complex_e1b = complex_caf_e1b[peak_idx_e1b]

                idx_interp_e1b, peak_interp_e1b = interpolate_caf_peak_1d(power_e1b, mode=interpolation_mode, peak_idx=peak_idx_e1b)

                # ── Same-period matching ──────────────────────────────────
                idx_interp_e1b_matched = idx_interp_e1b
                e6c_matched_period = None

                e6c_result_for_prn = None
                for _r in results_e6c:
                    if (_r.get("prn") == prn
                            and "error" not in _r
                            and "public" not in _r.get("mode", "").lower()):
                        e6c_result_for_prn = _r
                        break

                if e6c_result_for_prn is not None:
                    e6c_peak_idx = e6c_result_for_prn.get(
                        "peak_index_interp",
                        e6c_result_for_prn.get("peak_index", 0))
                    e6c_time_in_e1_samples = e6c_peak_idx * (fs_hz_e1 / fs_hz)
                    samples_per_period_e1b = int(round(
                        fs_hz_e1 / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS))
                    target_period = int(e6c_time_in_e1_samples) // samples_per_period_e1b

                    p_start = target_period * samples_per_period_e1b
                    p_end   = min(p_start + samples_per_period_e1b, len(power_e1b))

                    if p_end > p_start:
                        local_max = p_start + int(np.argmax(power_e1b[p_start:p_end]))
                        idx_interp_e1b_matched, _ = interpolate_caf_peak_1d(
                            power_e1b, mode=interpolation_mode, peak_idx=local_max)
                        e6c_matched_period = target_period
                        _debug(
                            f"  E1B PRN {prn}: Same-period match → "
                            f"E6C peak {e6c_peak_idx:.1f} → E1B period {target_period}, "
                            f"local peak {local_max}, interp {idx_interp_e1b_matched:.2f} "
                            f"(global {idx_interp_e1b:.2f}, "
                            f"delta={idx_interp_e1b - idx_interp_e1b_matched:.2f})")

                # PPSP
                exclusion_width_e1 = int(ppsp_exclusion_chips * (fs_hz_e1 / E1_CHIP_RATE_HZ))
                ppsp_db_e1b = calculate_ppsp_db(power_e1b, peak_idx_e1b, exclusion_width_e1, samples_per_code=samples_per_code_e1)

                # SNR
                noise_power_e1b = float(np.median(power_e1b))
                snr_like_e1b = peak_power_e1b / (noise_power_e1b + 1e-12)
                snr_output_db_e1b = 10.0 * math.log10(snr_like_e1b) if snr_like_e1b > 0 else float("-inf")

                # C/N0
                nscode_e1b = int(fs_hz_e1 / E1_CHIP_RATE_HZ * E1_CODE_LENGTH_CHIPS)
                cn0_dbhz_e1b = estimate_cn0_gonzalo_salcedo(
                    signal_rx=iq_full_e1,
                    caf_peak_complex=caf_peak_complex_e1b,
                    nscode=nscode_e1b,
                    nc=nc_coherent_aux, ni=ni_periods_aux, fs_hz=fs_hz_e1,
                    t_int_ref_ms=1.0
                )

                correlation_profiles[f"E1B_PRN{prn}"] = {
                    "power": power_e1b,
                    "fs_hz": fs_hz_e1,
                    "peak_idx": peak_idx_e1b,
                }

                results_e1.append({
                    "prn": prn,
                    "signal": "E1B",
                    "best_doppler_hz": best_doppler_e1b,
                    "peak_index": peak_idx_e1b,
                    "peak_index_interp": idx_interp_e1b,
                    "peak_index_interp_matched": idx_interp_e1b_matched,
                    "e6c_matched_period": e6c_matched_period,
                    "peak_power": peak_power_e1b,
                    "peak_power_interp": peak_interp_e1b,
                    "ppsp_db": ppsp_db_e1b,
                    "snr_like": snr_like_e1b,
                    "snr_output_db": snr_output_db_e1b,
                    "cn0_dbhz": cn0_dbhz_e1b,
                })

                _debug(f"  E1B PRN {prn}: PPSP={ppsp_db_e1b:.2f} dB, SNR={snr_output_db_e1b:.2f} dB, C/N0={cn0_dbhz_e1b:.2f} dB-Hz, Doppler={best_doppler_e1b:.1f} Hz")

            except Exception as e:
                _debug(f"Error processing E1B PRN {prn}: {e}")
                results_e1.append({
                    "prn": prn,
                    "signal": "E1B",
                    "error": str(e),
                })
    
    # ═══════════════════════════════════════════════════════════════════════════
    # Combine results and generate plot
    # ═══════════════════════════════════════════════════════════════════════════
    all_results = results_e1 + results_e6b + results_e6c
    
    # Build RECS info for the plot
    recs_info = {}
    if ecs_file:
        recs_info["ecs_path"] = ecs_file
        recs_info["source"] = "Local file"
    
    # Generate and save the correlation plot
    plot_path = save_correlation_plot(
        results=all_results,
        iq_file_path=str(iq_file_path),
        correlation_profiles=correlation_profiles,
        fs_hz=fs_hz,
        recs_info=recs_info,
        ni_periods_e6=ni_periods_e6,
        ni_periods_aux=ni_periods_aux,
    )
    
    # Separate E1 and E6 results for SatelliteAuthenticator
    e6_all_results = results_e6b + results_e6c
    
    return {
        "success": True,
        "results": all_results,
        "e1_results": results_e1,
        "e6_results": e6_all_results,
        "sample_rate_hz": fs_hz,
        "correlation_profiles": correlation_profiles,
        "plot_path": plot_path,
        "parameters": {
            "fs_hz": fs_hz,
            "doppler_min_hz": doppler_min,
            "doppler_max_hz": doppler_max,
            "doppler_step_hz": doppler_step,
            "interpolation_mode": interpolation_mode,
            "ppsp_exclusion_chips": ppsp_exclusion_chips,
        },
    }


############################################ PIPELINE EXECUTION (CONTRACT 1.0) ############################################

def _resolve_prn_list(prn_str: str, almanac_visible) -> str:
    """Resolve "auto"/empty PRN strings to a comma-separated list using the
    almanac visible-satellite list. Falls back to all 36 Galileo PRNs."""
    if prn_str and prn_str.strip().lower() not in ("", "auto"):
        return prn_str
    visible = almanac_visible or []
    if visible:
        import re as _re
        prns = []
        for sv in visible:
            m = _re.match(r'E?(\d+)', str(sv.get('id', '')))
            if m:
                prns.append(int(m.group(1)))
        if prns:
            return ",".join(str(p) for p in prns)
    return ",".join(str(p) for p in range(1, 37))


def run_pipeline(
    config: Optional[Mapping[str, Any]] = None,
    inputs: Optional[Mapping[str, Any]] = None,
    globals: Optional[Mapping[str, Any]] = None,
    logger=print,
) -> Dict[str, Any]:
    """Schema-driven entry point.
    consumes: almanac_visible_satellites, ecs_hex, ecs_path, sample_rate_hz,
              snapshot_path_e1, snapshot_path_e6,
              snapshot_gst_sec, gst_recs_start_s, total_uncertainty_ms
              (svid_to_search, svid_encrypted, auxiliary_mode, rf_input_path_*
               come from globals)
    produces: correlation_results
    artifacts: plot (auto_open)
    """
    inputs = dict(inputs or {})
    g = dict(globals or {})

    aux_mode = (g.get("auxiliary_mode") or "off")
    e1_aux = aux_mode in ("E1B", "E1C")

    # Snapshot paths: prefer ctx-published live ones, else ConfigInit paths.
    snap_e6 = inputs.get("snapshot_path_e6") or g.get("rf_input_path_e6") or None
    snap_e1 = (inputs.get("snapshot_path_e1") or g.get("rf_input_path_e1") or None) if e1_aux else None

    # PRN list: resolve "auto" against the almanac.
    prn_str = _resolve_prn_list(
        g.get("svid_to_search") or "",
        inputs.get("almanac_visible_satellites") or [],
    )

    raw = _run_pipeline_legacy(
        config=config,
        sample_rate_hz=inputs.get("sample_rate_hz"),
        ecs_hex=inputs.get("ecs_hex"),
        ecs_path=inputs.get("ecs_path"),
        snapshot_path_e1=snap_e1,
        snapshot_path_e6=snap_e6,
        auxiliary_mode=aux_mode,
        prn_list=prn_str,
        prn_encrypted_list=g.get("svid_encrypted") or "",
        # E6C/ECS aperiodic alignment — used to bound the acquisition search
        # window on the encrypted band. When any of these is None or 0, the
        # legacy pipeline falls back to a full code-period search.
        snapshot_gst_sec=inputs.get("snapshot_gst_sec"),
        gst_recs_start_s=inputs.get("gst_recs_start_s"),
        total_uncertainty_ms=inputs.get("total_uncertainty_ms"),
    )

    correlation_results = {
        "e1_results": raw.get("e1_results", []),
        "e6_results": raw.get("e6_results", []),
        "sample_rate_hz": raw.get("sample_rate_hz"),
        "correlation_profiles": raw.get("correlation_profiles", {}),
    }
    plot_path = raw.get("plot_path")
    return {
        "outputs": {
            "correlation_results": correlation_results,
        },
        "artifacts": {"plot": plot_path} if plot_path else {},
        "summary": {
            "success": raw.get("success"),
            "e1_count": len(correlation_results["e1_results"]),
            "e6_count": len(correlation_results["e6_results"]),
        },
        "data": raw,
    }


run_pipeline.__contract_version__ = "1.0"
