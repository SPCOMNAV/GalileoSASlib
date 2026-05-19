"""
Signal Correlation submodules for Galileo E1/E6 signal processing.

This package provides modular components for GNSS signal correlation:
- constants: Galileo E1/E6 signal parameters
- loading: IQ samples and spreading code loading
- signal_processing: Correlation, Doppler search, replica generation
- metrics: PPSP, SNR, C/N0, peak interpolation
- peak_detection: First peak detection for BOC signals
- visualization: Correlation plots and peak comparison figures
"""

from .constants import (
    E6_CHIP_RATE_HZ,
    E6_CODE_LENGTH_CHIPS,
    E6_CARRIER_HZ,
    E1_CHIP_RATE_HZ,
    E1_CODE_LENGTH_CHIPS,
    E1_CARRIER_HZ,
    F_CARRIER_E1,
    F_CARRIER_E6,
    SPEED_OF_LIGHT,
    E6_CODE_PERIOD_MS,
    E1_CODE_PERIOD_MS,
)

from .loading import (
    load_samples_sc16_window,
    load_chips_for_band,
    load_e6b_chips,
    load_e6c_chips,
    load_e1b_chips,
    load_e1c_chips,
    load_ecs_from_hex,
    load_ecs_from_results,
)

from .signal_processing import (
    generate_sampled_replica,
    generate_sampled_replica_boc,
    doppler_wipeoff,
    compute_correlation,
    correlate_full_signal,
    search_doppler_range,
    search_doppler_fine,
    AcquisitionResult,
    perform_acquisition,
)

from .metrics import (
    calculate_ppsp_db,
    calculate_snr_peak_over_median_excl,
    estimate_cn0_gonzalo_salcedo,
    interpolate_caf_peak_1d,
)

from .peak_detection import (
    find_first_peak,
)

from .visualization import (
    save_correlation_plot,
    generate_e1_e6_peak_comparison_matplotlib,
    generate_e1_e6_peak_comparison_centered,
)

__all__ = [
    # Constants
    "E6_CHIP_RATE_HZ", "E6_CODE_LENGTH_CHIPS", "E6_CARRIER_HZ",
    "E1_CHIP_RATE_HZ", "E1_CODE_LENGTH_CHIPS", "E1_CARRIER_HZ",
    "F_CARRIER_E1", "F_CARRIER_E6", "SPEED_OF_LIGHT",
    "E6_CODE_PERIOD_MS", "E1_CODE_PERIOD_MS",
    # Loading
    "load_samples_sc16_window", "load_chips_for_band",
    "load_e6b_chips", "load_e6c_chips", "load_e1b_chips", "load_e1c_chips",
    "load_ecs_from_hex", "load_ecs_from_results",
    # Signal processing
    "generate_sampled_replica", "generate_sampled_replica_boc",
    "doppler_wipeoff", "compute_correlation", "correlate_full_signal",
    "search_doppler_range", "search_doppler_fine",
    "AcquisitionResult", "perform_acquisition",
    # Metrics
    "calculate_ppsp_db", "calculate_snr_peak_over_median_excl",
    "estimate_cn0_gonzalo_salcedo", "interpolate_caf_peak_1d",
    # Peak detection
    "find_first_peak",
    # Visualization
    "save_correlation_plot",
    "generate_e1_e6_peak_comparison_matplotlib",
    "generate_e1_e6_peak_comparison_centered",
]
