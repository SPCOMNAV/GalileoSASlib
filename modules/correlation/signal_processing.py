"""
Signal processing functions for Galileo correlation.

Includes replica generation, Doppler wipe-off, and correlation (PCPS).

The core correlation follows the PCPS (Parallel Code Phase Search) method
described in:
  - R. Terris Gallego, "compute_correlation", v0.2
  - K. Borre et al., "A Software-Defined GPS and Galileo Receiver", 2007, Ch. 6.4
  - K. Borre et al., "GNSS Software Receivers", 2020, Ch. 9.2.9, p. 193
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple, Optional, List

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    np = None
    HAS_NUMPY = False

from .constants import E6_CHIP_RATE_HZ, E6_CARRIER_HZ


def _check_numpy() -> None:
    """Verify numpy is available."""
    if not HAS_NUMPY:
        raise RuntimeError("numpy is required for signal correlation but is not installed")


# ---------------------------------------------------------------------------
#  Replica generation
# ---------------------------------------------------------------------------

def generate_sampled_replica(chips_pm1, chip_rate_hz: float, fs_hz: float, num_samples: int):
    """
    Generate sampled replica of spreading code.

    Maps each sample index to the corresponding chip using nearest-neighbor
    interpolation (chip_idx = floor(n * Rc / Fs)).

    Args:
        chips_pm1: Spreading code chips in polar format (+1/-1)
        chip_rate_hz: Chip rate in Hz
        fs_hz: Sample rate in Hz
        num_samples: Number of samples to generate

    Returns:
        Sampled replica as np.float32 array
    """
    _check_numpy()

    L = len(chips_pm1)
    n = np.arange(num_samples)
    chip_idx = np.floor(n * (chip_rate_hz / fs_hz)).astype(np.int64)
    valid_indices = chip_idx < L
    replica = np.zeros(num_samples, dtype=np.float32)
    replica[valid_indices] = chips_pm1[chip_idx[valid_indices]]
    return replica


def generate_sampled_replica_boc(chips_pm1, chip_rate_hz: float, fs_hz: float,
                                  num_samples: int, boc_m: int = 1, boc_n: int = 1):
    """
    Generate sampled replica with BOC(m,n) modulation.

    BOC(m,n) uses a square-wave subcarrier at frequency m*1.023 MHz
    with chip rate n*1.023 MHz.

    For Galileo E1-OS: BOC(1,1) means subcarrier at 1.023 MHz alternating
    the sign of each chip at twice the chip rate.

    Args:
        chips_pm1: Spreading code chips in polar format (+1/-1)
        chip_rate_hz: Chip rate in Hz
        fs_hz: Sample rate in Hz
        num_samples: Number of samples to generate
        boc_m: BOC subcarrier multiplier (default 1 for BOC(1,1))
        boc_n: BOC chip rate multiplier (default 1 for BOC(1,1))

    Returns:
        Sampled replica with BOC modulation
    """
    _check_numpy()

    L = len(chips_pm1)
    n = np.arange(num_samples)
    chip_idx = np.floor(n * (chip_rate_hz / fs_hz)).astype(np.int64)
    valid_indices = chip_idx < L
    replica = np.zeros(num_samples, dtype=np.float32)
    replica[valid_indices] = chips_pm1[chip_idx[valid_indices]]

    # BOC subcarrier: c = [-1, +1], index = floor(2 * incr * n) % 2
    incr = chip_rate_hz / fs_hz
    c = np.array([-1, 1], dtype=np.float32)
    idx = np.floor(2 * incr * n).astype(int) % 2
    subcarrier = c[idx]

    return replica * subcarrier


# ---------------------------------------------------------------------------
#  Doppler wipe-off
# ---------------------------------------------------------------------------

def doppler_wipeoff(x, fs_hz: float, fd_hz: float, sample_offset: int = 0):
    """
    Remove carrier Doppler from signal by multiplying with a complex
    exponential at the estimated Doppler frequency.

    Args:
        x: Input IQ signal (complex)
        fs_hz: Sample rate in Hz
        fd_hz: Doppler frequency in Hz
        sample_offset: Absolute sample index of the first sample in *x*.
            Used for phase-continuous wipe-off across consecutive blocks
            (as in perform_acquisition).  Default 0 (relative indexing).

    Returns:
        Doppler-corrected signal (complex)
    """
    _check_numpy()
    n = np.arange(x.size) + sample_offset
    return x * np.exp(-2j * np.pi * fd_hz * (n / fs_hz))


# ---------------------------------------------------------------------------
#  Core correlation: PCPS  (Parallel Code Phase Search)
# ---------------------------------------------------------------------------

def compute_correlation(signal_rx, local_replica):
    """
    PCPS correlation between received signal and local replica.

    Implements the Parallel Code Phase Search method as described in
    R. Terris Gallego, "compute_correlation", v0.2.

    The correlation in time is performed in the frequency domain:

        corrSeq = (1/Ncode) * IFFT( FFT(signal_rx) * conj(FFT(replica)) )

    Both sequences are zero-padded to the next power-of-two length to
    avoid circular-convolution aliasing, following the overlap-save
    rationale described in [Borre2020, p. 194].

    The output is trimmed to `NsAcq - NscodeEq` samples (the valid
    linear-correlation region), matching Terris's convention.

    Note: unlike Terris's MATLAB code, no 1/NscodeEq normalization is
    applied here so that the absolute peak power is preserved for
    downstream metrics (C/N0, SNR).

    Args:
        signal_rx: Received signal after Doppler wipe-off (complex,
                   length NsAcq).
        local_replica: Sampled local replica (real or complex,
                       length NscodeEq).

    Returns:
        Complex correlation sequence, length = len(signal_rx) - len(local_replica).
    """
    _check_numpy()

    NsAcq = signal_rx.size
    NscodeEq = local_replica.size
    num_time_bins = NsAcq - NscodeEq

    if num_time_bins <= 0:
        return np.zeros(max(1, NsAcq), dtype=np.complex64)

    # FFT length: next power of two >= NsAcq + NscodeEq - 1
    # (ensures the linear cross-correlation is alias-free)
    Nfft = int(2 ** np.ceil(np.log2(NsAcq + NscodeEq - 1)))

    # Zero-pad replica and compute its FFT once
    replica_padded_ft = np.fft.fft(local_replica, n=Nfft)

    # FFT of received signal
    signal_ft = np.fft.fft(signal_rx, n=Nfft)

    # Circular correlation in frequency domain
    corr_full = np.fft.ifft(signal_ft * np.conj(replica_padded_ft))

    # Trim to valid region (discard circular aliasing tail)
    return corr_full[:num_time_bins]


# ---------------------------------------------------------------------------
#  High-level correlation wrappers
# ---------------------------------------------------------------------------

def correlate_full_signal(iq_full, fs_hz: float, doppler_hz: float,
                          replica_chips, chip_rate_hz: float, use_boc: bool = False,
                          return_complex: bool = False,
                          correct_code_doppler: bool = False,
                          carrier_freq_hz: float = None):
    """
    Correlate full IQ signal with replica using PCPS.

    Performs Doppler wipe-off, generates the sampled replica (with
    optional code-Doppler correction and BOC modulation), and calls
    compute_correlation.

    Args:
        iq_full: Full IQ signal (complex)
        fs_hz: Sample rate in Hz
        doppler_hz: Doppler frequency for wipe-off
        replica_chips: Spreading code chips in polar format (+1/-1)
        chip_rate_hz: Nominal chip rate in Hz
        use_boc: If True, apply BOC(1,1) modulation to replica (for E1)
        return_complex: If True, return tuple (power, complex_caf)
        correct_code_doppler: If True, adjust replica chip rate for Doppler
        carrier_freq_hz: Carrier frequency (required when correct_code_doppler=True)

    Returns:
        Power profile (|corr|^2) with length = len(iq_full) - Nscode.
        If return_complex=True, returns tuple (power, complex_corr).
    """
    _check_numpy()

    # Code Doppler correction: f_chip_rx = f_chip * (1 + f_d / f_carrier)
    eff_chip_rate = chip_rate_hz
    if correct_code_doppler and carrier_freq_hz is not None and carrier_freq_hz > 0:
        eff_chip_rate = chip_rate_hz * (1.0 + doppler_hz / carrier_freq_hz)

    num_samples_replica = int(round(len(replica_chips) * fs_hz / eff_chip_rate))

    if use_boc:
        replica = generate_sampled_replica_boc(replica_chips, eff_chip_rate, fs_hz, num_samples_replica)
    else:
        replica = generate_sampled_replica(replica_chips, eff_chip_rate, fs_hz, num_samples_replica)

    # Doppler wipe-off
    iq_wiped = doppler_wipeoff(iq_full, fs_hz, doppler_hz)

    # PCPS correlation (Terris method)
    corr_seq = compute_correlation(iq_wiped, replica)

    power = np.abs(corr_seq) ** 2

    if return_complex:
        return power, corr_seq
    return power


# ---------------------------------------------------------------------------
#  Doppler search
# ---------------------------------------------------------------------------

def search_doppler_range(
    iq_segment,
    fs_hz: float,
    replica_chips,
    doppler_min: float,
    doppler_max: float,
    doppler_step: float,
    chip_rate_hz: float = None,
    carrier_freq_hz: float = None,
    use_boc: bool = False,
    correct_code_doppler: bool = False
) -> Tuple[float, float]:
    """
    Coarse Doppler search over a frequency range (CAF frequency axis).

    For each candidate Doppler frequency:
      1. Wipe-off carrier Doppler
      2. Compute PCPS correlation (all code-phase bins in parallel)
      3. Keep the frequency with the highest correlation peak

    Args:
        iq_segment: IQ samples for Doppler search
        fs_hz: Sample rate in Hz
        replica_chips: Spreading code chips (+1/-1)
        doppler_min/max/step: Doppler search grid [Hz]
        chip_rate_hz: Nominal chip rate (default E6)
        carrier_freq_hz: Carrier frequency (unused, kept for API compat)
        use_boc: Use BOC modulation for replica
        correct_code_doppler: Ignored during search

    Returns:
        Tuple (best_doppler_hz, best_peak_power)
    """
    _check_numpy()

    if chip_rate_hz is None:
        chip_rate_hz = E6_CHIP_RATE_HZ

    # Generate sampled replica ONCE (no Doppler correction during search)
    num_samples_replica = int(round(len(replica_chips) * fs_hz / chip_rate_hz))
    if use_boc:
        replica = generate_sampled_replica_boc(replica_chips, chip_rate_hz, fs_hz, num_samples_replica)
    else:
        replica = generate_sampled_replica(replica_chips, chip_rate_hz, fs_hz, num_samples_replica)

    # Pre-compute replica FFT (reused across all Doppler bins)
    Nfft = int(2 ** np.ceil(np.log2(iq_segment.size)))
    replica_ft_conj = np.conj(np.fft.fft(replica, n=Nfft))
    NscodeEq = replica.size
    num_time_bins = iq_segment.size - NscodeEq

    if num_time_bins <= 0:
        return 0.0, 0.0

    best_peak_power = -float("inf")
    best_doppler = 0.0

    stop_fd = doppler_max + round(doppler_step / 2)
    for fd in np.arange(doppler_min, stop_fd, doppler_step):
        iq_wiped = doppler_wipeoff(iq_segment, fs_hz, fd)
        signal_ft = np.fft.fft(iq_wiped, n=Nfft)
        corr = np.fft.ifft(signal_ft * replica_ft_conj)
        power = np.abs(corr[:num_time_bins]) ** 2

        peak = float(np.max(power))
        if peak > best_peak_power:
            best_peak_power = peak
            best_doppler = float(fd)

    return best_doppler, best_peak_power


def search_doppler_fine(
    iq_segment,
    fs_hz: float,
    replica_chips,
    coarse_doppler_hz: float,
    fine_range_hz: float,
    chip_rate_hz: float,
    carrier_freq_hz: float,
    ni_periods: int = 20,
    nc_coherent: int = 1,
    use_boc: bool = False,
    correct_code_doppler: bool = False
) -> Tuple[float, float]:
    """
    Fine Doppler search around a coarse estimate with coherent/non-coherent
    integration, following the structure in Terris's perform_acquisition.

    Integration is structured as Nc coherent x Ni non-coherent blocks:
      - Nc code periods are correlated as a single block (coherent) -> |sum|^2
      - Ni such blocks are summed non-coherently (power sum)
    Total integration time: Tint = Nc x Ni x Tcode

    Step size is 1 Hz for precise Doppler estimation.

    Args:
        iq_segment: IQ samples for search
        fs_hz: Sample rate
        replica_chips: Spreading code (one period, +1/-1)
        coarse_doppler_hz: Coarse Doppler estimate from initial search
        fine_range_hz: Search range +/- around coarse estimate [Hz]
        chip_rate_hz: Nominal chip rate
        carrier_freq_hz: Carrier frequency
        ni_periods: Number of non-coherent integration blocks (Ni)
        nc_coherent: Number of code periods per coherent block (Nc)
        use_boc: Use BOC modulation
        correct_code_doppler: Ignored (correction applied after acquisition)

    Returns:
        Tuple (refined_doppler_hz, peak_power)
    """
    _check_numpy()

    if nc_coherent < 1:
        nc_coherent = 1

    # Code period duration and samples
    t_code = len(replica_chips) / chip_rate_hz
    samples_per_period = int(fs_hz * t_code)

    # Total periods needed = Nc x Ni; limit to available data
    t_int_iq = len(iq_segment) / fs_hz
    max_periods = int(t_int_iq / t_code)
    total_periods = min(nc_coherent * ni_periods, max_periods)
    actual_ni = max(1, total_periods // nc_coherent)

    # Coherent block = Nc code periods
    samples_per_block = nc_coherent * samples_per_period

    # Build replica for one coherent block (Nc periods)
    if use_boc:
        replica_one = generate_sampled_replica_boc(replica_chips, chip_rate_hz, fs_hz, samples_per_period)
    else:
        replica_one = generate_sampled_replica(replica_chips, chip_rate_hz, fs_hz, samples_per_period)

    replica = np.tile(replica_one, nc_coherent) if nc_coherent > 1 else replica_one

    # Pre-compute replica FFT for the block size
    Nfft = int(2 ** np.ceil(np.log2(samples_per_block)))
    replica_ft_conj = np.conj(np.fft.fft(replica, n=Nfft))
    NscodeEq = replica.size

    # Search grid
    fine_step = 1.0
    doppler_min = coarse_doppler_hz - fine_range_hz
    doppler_max = coarse_doppler_hz + fine_range_hz

    best_peak_power = -float("inf")
    best_doppler = coarse_doppler_hz

    stop_fd = doppler_max + round(fine_step / 2)
    for fd in np.arange(doppler_min, stop_fd, fine_step):
        iq_wiped = doppler_wipeoff(iq_segment, fs_hz, fd)

        total_peak_power = 0.0
        valid_blocks = 0

        for blk in range(actual_ni):
            start = blk * samples_per_block
            end = start + samples_per_block
            if end > iq_wiped.size:
                break

            # PCPS correlation for this coherent block
            signal_ft = np.fft.fft(iq_wiped[start:end], n=Nfft)
            corr = np.fft.ifft(signal_ft * replica_ft_conj)
            power = np.abs(corr[:samples_per_block - NscodeEq] if samples_per_block > NscodeEq
                           else corr) ** 2

            total_peak_power += float(np.max(power))
            valid_blocks += 1

        if valid_blocks > 0 and total_peak_power > best_peak_power:
            best_peak_power = total_peak_power
            best_doppler = float(fd)

    return best_doppler, best_peak_power


# ---------------------------------------------------------------------------
#  CAF acquisition: perform_acquisition  (Terris, v0.2)
# ---------------------------------------------------------------------------

@dataclass
class AcquisitionResult:
    """Cross Ambiguity Function (CAF) results from perform_acquisition.

    Following R. Terris Gallego, "perform_acquisition", v0.2.

    Attributes:
        caf_seq:  Last single-period CAF, complex
                  (num_time_bins × num_freq_bins).
        caf_coh:  Coherent integration across Nc periods, complex
                  (num_time_bins × num_freq_bins).
        caf_pdi:  Post-detection integration across Nc × NPdi periods
                  (num_time_bins × num_freq_bins).
                  Real for 'NPDI' method, complex for 'Coh'.
        doppler_bins: Doppler frequency search grid [Hz].
        caf_seq_all:  All per-period CAFs when *save_all_cafs=True*
                      (num_int × num_time_bins × num_freq_bins), else None.
    """
    caf_seq: object
    caf_coh: object
    caf_pdi: object
    doppler_bins: object
    caf_seq_all: object = None


def perform_acquisition(
    signal_rx,
    replica_chips,
    fs_hz: float,
    doppler_bins,
    chip_rate_hz: float,
    carrier_freq_hz: float = 0.0,
    nc_coherent: int = 1,
    n_pdi: int = 1,
    exp_n_coh: float = 2.0,
    pdi_method: str = 'NPDI',
    correct_code_doppler: bool = False,
    use_boc: bool = False,
    boc_m: int = 1,
    boc_n: int = 1,
    save_all_cafs: bool = False,
    cp_est_ini: int = 0,
    ns_acq: int = 0,
    replicas_per_period=None,
    recs_delays_samples=None,
) -> AcquisitionResult:
    """
    Compute the Cross Ambiguity Function (CAF) with coherent and
    post-detection integration.

    Implements the acquisition CAF following the structure described in
    R. Terris Gallego, "perform_acquisition", v0.2.

    Integration structure (Nc coherent × NPdi non-coherent)::

        for nPdi in 0..NPdi-1:
            cafCoh = 0                          # complex accumulator
            for nCoh in 0..Nc-1:
                nInt = nPdi * Nc + nCoh
                cafSeq = PCPS_over_doppler_grid  # single-period CAF
                cafCoh += cafSeq                 # coherent sum
            if NPDI:
                cafPdi += |cafCoh|^expNCoh       # power accumulation
            else (Coh):
                cafPdi += cafCoh                 # complex accumulation

    Phase-continuous Doppler wipe-off is maintained across integration
    blocks by using absolute sample indices::

        samplePoints = [0 .. NsAcq-1] + nInt * NsSeq

    Args:
        signal_rx: Full IQ signal (complex 1-D).  Must be long enough for
            ``(numInt - 1) * NsBlock + cp_est_ini + NsAcq`` samples.
        replica_chips: Spreading code chips (+1/-1), one code period.
            Ignored when *replicas_per_period* is provided.
        fs_hz: Sample rate [Hz].
        doppler_bins: 1-D array of Doppler frequencies to search [Hz].
        chip_rate_hz: Nominal chip rate [Hz].
        carrier_freq_hz: Carrier frequency [Hz].  Required when
            *correct_code_doppler* is True.
        nc_coherent: Number of coherent integrations per PDI block (Nc).
        n_pdi: Number of post-detection integration blocks (NPdi).
        exp_n_coh: Exponent for NPDI method (default 2 → power).
        pdi_method: ``'NPDI'`` (default) or ``'Coh'``.
        correct_code_doppler: Adjust replica chip rate per Doppler bin
            via ``Rcd = Rc × (1 + Fd / Fc)``.
        use_boc: Apply BOC modulation to replica.
        boc_m, boc_n: BOC modulation parameters (default 1,1).
        save_all_cafs: Store every per-period CAF for debug/test.
        cp_est_ini: Initial code-phase estimate [samples] (window offset).
        ns_acq: Acquisition window size [samples].  0 → auto
            (``2 × NsSeq``, clamped to available signal).
        replicas_per_period: For ACAS/RECS — list of chip arrays, one per
            integration period.  Overrides *replica_chips*.
        recs_delays_samples: For ACAS — per-period delay offsets [samples].

    Returns:
        :class:`AcquisitionResult` with CAF matrices and Doppler grid.
    """
    _check_numpy()

    doppler_bins = np.asarray(doppler_bins, dtype=np.float64)
    num_freq_bins = len(doppler_bins)
    num_int = nc_coherent * n_pdi

    # --- replica parameters ---
    acas_mode = replicas_per_period is not None
    chips_ref = replicas_per_period[0] if acas_mode else replica_chips
    code_len = len(chips_ref)

    # Samples per code period (nominal)
    ns_seq = int(round(code_len * fs_hz / chip_rate_hz))
    ns_block = ns_seq                       # one code period per block

    # Acquisition window size
    if ns_acq <= 0:
        ns_acq_auto = 2 * ns_seq            # full code-phase range
    else:
        ns_acq_auto = ns_acq

    # Clamp to available signal
    last_window_start = (num_int - 1) * ns_block + cp_est_ini
    if acas_mode and recs_delays_samples is not None:
        last_window_start += int(max(recs_delays_samples))
    max_available = len(signal_rx) - last_window_start
    ns_acq_eff = min(ns_acq_auto, max_available)

    # Edge case: not enough signal
    if ns_acq_eff <= ns_seq:
        nt = max(1, ns_acq_eff)
        empty = np.zeros((nt, num_freq_bins), dtype=np.complex64)
        return AcquisitionResult(
            caf_seq=empty.copy(),
            caf_coh=empty.copy(),
            caf_pdi=np.zeros((nt, num_freq_bins), dtype=np.float64),
            doppler_bins=doppler_bins,
        )

    num_time_bins = ns_acq_eff - ns_seq
    Nfft = int(2 ** np.ceil(np.log2(ns_acq_eff)))

    # --- pre-compute replica FFT when it is constant across all bins ---
    # (conventional mode, no code-Doppler correction)
    can_precompute = not correct_code_doppler and not acas_mode
    if can_precompute:
        if use_boc:
            base_replica = generate_sampled_replica_boc(
                replica_chips, chip_rate_hz, fs_hz, ns_seq, boc_m, boc_n)
        else:
            base_replica = generate_sampled_replica(
                replica_chips, chip_rate_hz, fs_hz, ns_seq)
        base_replica_ft_conj = np.conj(np.fft.fft(base_replica, n=Nfft))

    # --- initialise output CAFs ---
    if pdi_method == 'Coh':
        caf_pdi = np.zeros((num_time_bins, num_freq_bins), dtype=np.complex128)
    else:
        caf_pdi = np.zeros((num_time_bins, num_freq_bins), dtype=np.float64)

    caf_seq_all_list: Optional[list] = [] if save_all_cafs else None
    caf_seq = np.zeros((num_time_bins, num_freq_bins), dtype=np.complex128)
    caf_coh = np.zeros((num_time_bins, num_freq_bins), dtype=np.complex128)

    # ================================================================
    #  Main integration loops  (NPdi × Nc)
    # ================================================================
    for n_pdi_idx in range(n_pdi):

        # Reset coherent accumulator
        caf_coh[:] = 0

        for n_coh_idx in range(nc_coherent):
            n_int = n_pdi_idx * nc_coherent + n_coh_idx  # 0-based

            # --- acquisition window start ---
            n_acq_ini = n_int * ns_block + cp_est_ini
            if acas_mode and recs_delays_samples is not None:
                n_acq_ini += int(recs_delays_samples[n_int])

            # Extract signal window
            sig_end = n_acq_ini + ns_acq_eff
            if sig_end > len(signal_rx):
                break
            signal_acq = signal_rx[n_acq_ini:sig_end]

            # Absolute sample offset for phase-continuous Doppler wipe-off
            sample_offset = n_int * ns_seq

            # Replica chips for this period
            if acas_mode:
                period_chips = replicas_per_period[n_int % len(replicas_per_period)]
            else:
                period_chips = replica_chips

            # Per-period replica pre-compute (ACAS without code Doppler)
            if acas_mode and not correct_code_doppler:
                if use_boc:
                    period_replica = generate_sampled_replica_boc(
                        period_chips, chip_rate_hz, fs_hz, ns_seq, boc_m, boc_n)
                else:
                    period_replica = generate_sampled_replica(
                        period_chips, chip_rate_hz, fs_hz, ns_seq)
                period_replica_ft_conj = np.conj(
                    np.fft.fft(period_replica, n=Nfft))

            # Reset single-period CAF
            caf_seq[:] = 0

            # --- frequency-bin loop ---
            for f_idx, fd in enumerate(doppler_bins):

                # Phase-continuous Doppler wipe-off
                signal_wiped = doppler_wipeoff(
                    signal_acq, fs_hz, fd, sample_offset=sample_offset)

                # Select or compute replica FFT for this bin
                if can_precompute:
                    replica_ft_conj = base_replica_ft_conj
                elif acas_mode and not correct_code_doppler:
                    replica_ft_conj = period_replica_ft_conj
                else:
                    # Need fresh replica (code-Doppler corrected)
                    rcd = chip_rate_hz * (1.0 + fd / carrier_freq_hz) \
                        if carrier_freq_hz > 0 else chip_rate_hz
                    ns_rep = int(round(len(period_chips) * fs_hz / rcd))
                    if use_boc:
                        rep = generate_sampled_replica_boc(
                            period_chips, rcd, fs_hz, ns_rep, boc_m, boc_n)
                    else:
                        rep = generate_sampled_replica(
                            period_chips, rcd, fs_hz, ns_rep)
                    replica_ft_conj = np.conj(np.fft.fft(rep, n=Nfft))

                # PCPS correlation (inlined for efficiency)
                signal_ft = np.fft.fft(signal_wiped, n=Nfft)
                corr = np.fft.ifft(signal_ft * replica_ft_conj)
                caf_seq[:, f_idx] = corr[:num_time_bins]

            # Verify CAF is finite
            if not np.all(np.isfinite(caf_seq)):
                import warnings
                warnings.warn("CAF contains non-finite values at "
                              f"integration {n_int}")

            # Store intermediate CAF
            if caf_seq_all_list is not None:
                caf_seq_all_list.append(caf_seq.astype(np.complex64).copy())

            # Coherent integration: complex accumulation
            caf_coh += caf_seq

        # Post-detection integration
        if pdi_method == 'NPDI':
            caf_pdi += np.abs(caf_coh) ** exp_n_coh
        elif pdi_method == 'Coh':
            caf_pdi += caf_coh
        else:
            raise ValueError(f"Unknown pdi_method '{pdi_method}'. "
                             "Use 'NPDI' or 'Coh'.")

    # --- build output ---
    caf_seq_all = None
    if caf_seq_all_list:
        caf_seq_all = np.stack(caf_seq_all_list, axis=0)

    return AcquisitionResult(
        caf_seq=caf_seq.astype(np.complex64),
        caf_coh=caf_coh.astype(np.complex64),
        caf_pdi=caf_pdi,
        doppler_bins=doppler_bins,
        caf_seq_all=caf_seq_all,
    )
