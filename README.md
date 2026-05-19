# Galileo SASlib

Reference Python implementation of the Galileo SAS (Signal Authentication Service) processing chain. Decrypts the ECS spreading sequence from a RECS file using the matching OSNMA TESLA key, correlates it against the encrypted E6C signal in a recorded IQ snapshot, and produces a sample-accurate authentication metric.

Version: **1.0**

## Pipeline

The execution order is fixed (see `SASpipeline.PIPELINE_ORDER`):

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

Each block exposes a uniform `run_pipeline(config, inputs, globals, logger)` entry point that returns `{outputs, artifacts, summary, data}` and declares `__contract_version__ = "1.0"`. The schema-driven executor at `utils/executor.py` wires `consumes`/`produces` keys between blocks.

## Requirements

- Python 3.10 or newer
- `numpy`, `requests`, `dash`, `dash-bootstrap-components`, `dash-mantine-components`, `dash-diagram` (ReactFlow), `plotly`
- AES backend: `pycryptodome` **or** `cryptography`

Install dependencies:

```bash
pip install numpy requests dash dash-bootstrap-components dash-mantine-components dash-diagram plotly pycryptodome
```

## Usage

### Graphical interface

```bash
python SASgraphicInterface.py
```

Open `http://127.0.0.1:8050` in a browser. The flow:

1. Configure each block in the graph (or import a saved pipeline JSON).
2. Press **Start pipeline**.
3. Inspect block outputs and correlation plots in the result panels.

### Programmatic execution

```python
from SASpipeline import PIPELINE_ORDER, get_module_run_pipeline
from utils.pipeline_configs import load_pipeline_config, apply_pipeline_config

cfg = load_pipeline_config("my_config.json")
apply_pipeline_config(cfg)
# ... drive the executor (see utils/executor.py) or run blocks individually.
```

## Layout

| Path | Purpose |
|------|---------|
| `modules/` | One file per pipeline block (`Module_<Name>.py`) plus the `correlation/` sub-package |
| `default_json/` | Block schemas (inputs, outputs, defaults, UI fields) |
| `config_json/` | Editable per-block configuration consumed at runtime |
| `ui/` | Dash-based graphical interface (callbacks, components, services) |
| `utils/` | Shared helpers: schema-driven executor, paths, GST/UTC conversions, BladeRF API |
| `SASpipeline.py` | Module registry, pipeline order, snapshot/save helpers |
| `SASgraphicInterface.py` | Launcher for the Dash UI |

## Configuration

Each block reads its JSON config from `config_json/<BlockName>.json`. Schemas live in `default_json/<BlockName>_schema.json` and declare:

- `consumes` / `produces` — context keys exchanged with upstream/downstream blocks
- `parameters` — editable fields, types, defaults, UI rendering hints
- `execution` — parallelism and required-when guards

Pipeline-level snapshots (the full set of block configurations plus run metadata) can be exported and re-imported via the GUI's *Save / Import Config* actions, or programmatically through `utils.pipeline_configs`.
