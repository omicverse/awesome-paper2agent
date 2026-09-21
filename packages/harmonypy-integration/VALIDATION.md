# Validation

Generated from a Paper2MCP delivery (`harmonypy`) on 2026-09-21.

## How this was verified

- Runtime: Python 3.12; `src/requirements.txt` pins 4 distribution(s).
- Tool inventory reconciled by the build pipeline (2 tool(s)): `run_harmony`, `compute_lisi`.
- Strict real-call validation passed in the project environment and in a clean environment rebuilt from `src/requirements.txt` (7 case(s)).
- Acceptance calls recorded by the pipeline's independent verifier:
- `run_harmony` — case `run-harmony-pbmc-reference`: asserts {"n_batches":3,"n_cells":3500,"n_pcs":30}; requires 1 artifact(s)
- `run_harmony` — case `run-harmony-pbmc-repeat`: asserts {"n_batches":3,"n_cells":3500}; requires 1 artifact(s)
- `run_harmony` — case `run-harmony-missing-batch-column`: error path, expects `is not a column in the metadata table`.
- `run_harmony` — case `run-harmony-missing-input-file`: error path, expects `is not a readable file`.
- `compute_lisi` — case `compute-lisi-fixture-label1`: asserts {"n_cells":400,"n_labels":2}; requires 1 artifact(s)
- `compute_lisi` — case `compute-lisi-fixture-label2-repeat`: asserts {"n_cells":400,"n_labels":2}; requires 1 artifact(s)
- `compute_lisi` — case `compute-lisi-missing-label-column`: error path, expects `is not a column in the metadata table`.
- Delivery report `reports/delivery-validation.json` (sha256 `0f0957c10467b9034a355ed14b8f20968019411388df08ef251f4942d4f9f624`) records the extracted-archive reinstall and the per-tool calls made from it.

## What remains unverified

- Agreement with numbers in the paper beyond the upstream tracked reference data.
- Datasets larger than the tracked fixtures, multi-column batch correction, Windows and GPU execution.
