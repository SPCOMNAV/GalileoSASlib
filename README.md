Galileo SASlib
==============

Galileo SASlib is an open-source Python library for the end-to-end processing of the Galileo Signal Authentication Service (SAS). It records or ingests an E1/E6 IQ snapshot, retrieves the matching RECS unitary and OSNMA TESLA key from public Galileo Service Centre (GSC) and OSNMAlib (https://osnmalib.eu/) interfaces, decrypts the encrypted spreading sequence (ECS), correlates it against the recorded signal, and produces a sample-accurate authentication metric for each tracked satellite. A schema-driven pipeline ties the 13 processing blocks together and a Dash-based graphical interface lets the user configure, run, and inspect each step.

The implementation follows the Galileo SAS Receiver Guidelines: cryptographic material handling, RECS / ECS unpacking, code-phase assist (ACAS), uncertainty propagation, and the E6C residual range authentication metric defined in the official SAS paper. The library has been validated with real time test recorded from a BladeRF over the E1 and E6 bands.

The default time synchronization assumes a recorded snapshot where the IQ timestamp is known with sub-millisecond accuracy; for live snapshots the receiver clock is disciplined with NTP at acquisition time. For a discussion of the underlying signal model, see the Galileo SAS specification and the Galileo OSNMA Receiver Guidelines.

Version: **1.0**

Supports Python 3.10, 3.11 and 3.12. Tested on Linux and Windows.

Galileo SASlib Features
---

### Features supported:

  * Acquisition of E1B/E1C and E6B public spreading codes (single-band and multi-band modes).
  * Decryption of the encrypted spreading sequence (ECS) from a RECS unitary using the corresponding OSNMA TESLA key.
  * Correlation of the decrypted ECS replica against the E6C component of a recorded IQ snapshot.
  * Per-PRN computation of the residual range authentication metric, with configurable σ and confidence level.
  * RECS aggregated-to-unitary parsing, RECS file selection by `target_epoch_override`, and KDI/subframe-aware TESLA key lookup.
  * Almanac-driven visibility filter and skyplot generation for the snapshot epoch.
  * Broadcast ephemerides download and parsing from ESA GSSC (hourly and daily RINEX-3).
  * Snapshot ingestion from local file or live recording through the BladeRF UI API.
  * Time-reference synchronization via NTP with configurable server and retry policy.
  * Uncertainty budget propagation: satellite clock, receiver clock and propagation jitter combined into a code-phase search window for the ACAS assist.
  * Snapshot-PVT estimation with ionospheric, multipath and hardware bias models.

### Cryptographic and authentication operations:

  * Validation of the RECS filename and header (provider, SVID, KDI, NChip, RTBA, ATOW).
  * AES-256-CTR decryption of the RECS body using the OSNMA TESLA key and the subframe-aligned P vector.
  * Subframe-aligned TESLA key lookup (KDI=0 / KDI=1 selection).
  * Re-export of the decrypted ECS as `.bin` chip stream for downstream blocks.
  * E6C authentication with the `gamma_auth` threshold (kappa × σ) per the SAS paper Eq. 14.

### Inputs supported:

  * E1 and E6 IQ snapshots in `sc16` 12-bit format (BladeRF, 20 MSPS default).
  * Aggregated RECS files (`.bin`) from GSC X01 (auto-decomposed into unitary `.RCS` files).
  * Individual unitary RECS files (`.RCS`).
  * OSNMA TESLA keys in JSON (simple `{epoch, osnma_key}` format), XML (OSNMAlib schema) or CSV.
  * Broadcast ephemerides in RINEX-3 navigation format (`.rnx` / `.rnx.gz`).
  * GSC Europa XML almanac.

### Future development:

  * Integration E5 band.
  * Standard outputs.
  * Spoofing algorithms implementation: MPD, VSS, SQM, C/n0, AGC.
  * Wiki page.
  

Documentation
---

  * This README file.
  * Block-level documentation lives in each `default_json/<BlockName>_schema.json` (declared inputs, outputs, UI fields and parameter descriptions).
  * General Galileo SAS documentation: [GSC website with the reference documents](https://www.gsc-europa.eu/electronic-library/programme-reference-documents). Look at the SAS Service Definition Document, the OSNMA ICD, and the OSNMA Receiver Guidelines.

Quick Run - Try it!
===

Requirements
---

All required Python libraries can be installed with `pip`:

```
$ pip install numpy requests dash dash-bootstrap-components dash-mantine-components dash-diagram plotly pycryptodome
```

The AES backend can be either `pycryptodome` (default) or `cryptography`. Install only one.

Graphical interface
---

The recommended way to run Galileo SASlib is through the graphical interface launcher.

```
$ python SASgraphicInterface.py
```

Open `http://127.0.0.1:8050` in a browser. The interface shows the 13 pipeline blocks as nodes in a flow diagram. The flow:

1. Click a block to open its configuration form, or use **Import Config** to load a saved pipeline JSON from `pipeline_configs/`.
2. Configure each block (RF paths, RECS/TESLA sources, integration times, Doppler search, σ values...).
3. Press **Start pipeline** and observe progress in the terminal panel.
4. Inspect block outputs, correlation profiles and the E1B vs E6B comparison plot in the result panels.

Pipeline configurations can be exported as JSON snapshots from the GUI for later reruns. Pre-baked snapshots in `pipeline_configs/` cover the live capture, ION 2024 dataset, and the GSC SAS server reference data.

Programmatic execution
---

Blocks can also be driven directly without the GUI:

```python
from SASpipeline import PIPELINE_ORDER, get_module_run_pipeline
from utils.pipeline_configs import load_pipeline_config, apply_pipeline_config

cfg = load_pipeline_config("my_config.json")
apply_pipeline_config(cfg)
# Drive the executor (see utils/executor.py) or run blocks individually.
```

Pipeline
===

The execution order is fixed (see `SASpipeline.PIPELINE_ORDER`) consider this may change on the first phase:

```
ConfigInit
TimeReferenceSynchronizer
AlmanacManagement
BGDandRECSdownloader
BGDandRECSparser
UncertaintyModule
SnapshotRecording
TESLAkeyManagement
RECSDecryption
SignalCorrelation
EphemeridesManagement
SatelliteAuthenticator
PVTComputation
```

Each block exposes a uniform `run_pipeline(config, inputs, globals, logger)` entry point that returns `{outputs, artifacts, summary, data}` and declares `__contract_version__ = "1.0"`. The schema-driven executor at `utils/executor.py` wires `consumes` / `produces` keys between blocks and validates contract versions at runtime.

Project Layout
===

| Path | Purpose |
|------|---------|
| `modules/` | One file per pipeline block (`Module_<Name>.py`) plus the `correlation/` sub-package |
| `default_json/` | Block schemas (inputs, outputs, defaults, UI fields) |
| `config_json/` | Editable per-block configuration consumed at runtime |
| `ui/` | Dash-based graphical interface (callbacks, components, services) |
| `utils/` | Shared helpers: schema-driven executor, paths, GST/UTC conversions, BladeRF API |
| `SASpipeline.py` | Module registry, pipeline order, snapshot/save helpers |
| `SASgraphicInterface.py` | Launcher for the Dash UI |

Galileo SASlib Configuration Options
===

Each block reads its JSON config from `config_json/<BlockName>.json`. Schemas live in `default_json/<BlockName>_schema.json` and declare:

  * `consumes` / `produces` — context keys exchanged with upstream / downstream blocks.
  * `parameters` — editable fields, types, defaults, and UI rendering hints.
  * `execution` — parallelism guards and required-when conditions.

Pipeline-level snapshots (the full set of 13 block configurations plus run metadata) can be exported and re-imported through the GUI's *Save / Import Config* actions, or programmatically through `utils.pipeline_configs`. The same JSON format is used by the snapshots stored under `executions/<run_id>/execution.json`.

The most relevant parameters at the user level are:

  * `ConfigInit.gst_start_time_sec`: GST seconds at the start of the IQ snapshot (mandatory for replayed snapshots).
  * `ConfigInit.svid_to_search`: comma-separated PRN list, or `auto` to use the almanac-visible set.
  * `ConfigInit.svid_encrypted`: PRNs for which the E6C correlation must use the decrypted ECS.
  * `ConfigInit.rf_input_path_e1` / `rf_input_path_e6`: paths to the recorded IQ files.
  * `RECSDecryption.target_epoch_override`: GST epoch of the RECS unitary to decrypt; the parser picks the matching `.RCS` file automatically.
  * `SignalCorrelation.start_sample`, `num_samples`: window of the IQ snapshot to process.
  * `SignalCorrelation.ni_periods_e6`, `ni_periods_aux`: non-coherent integration depth for E6 and auxiliary (E1) bands.
  * `SignalCorrelation.doppler_min_hz`, `doppler_max_hz`, `doppler_step_hz`: coarse Doppler search range and grid.
  * `UncertaintyModule.satellite_clock_uncertainty_ms`, `receiver_clock_uncertainty_ms`, `propagation_uncertainty_ms`: per-component code-phase uncertainty (combined into `total_uncertainty_ms`).
  * `SatelliteAuthenticator.kappa`, `confidence_level_percent`: authentication threshold parameters.

Logging
---

Each block logs through the shared `console_log` helper, which routes both to the terminal and to the GUI log panel. Block results are persisted to `executions/<run_id>/`, including:

  * `execution.json` — the full pipeline configuration used for the run.
  * `plots/` — correlation HTML / PNG plots produced by the SignalCorrelation block.
  * Per-block intermediate artifacts (decrypted ECS, parsed unitary RECS files, etc.).

The authentication result for each PRN is written to `results/authentication/authentication_<timestamp>.json`.

License
===

The project is licensed under the EUPL-1.2 license.

Disclaimer
===

Galileo SASlib is provided for research, education, and integration purposes. Users use it at their own risk, without any guarantee or liability from the code authors or the Galileo signal provider.
