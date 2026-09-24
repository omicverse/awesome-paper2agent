# harmonypy MCP package

MCP tools for Harmony batch-effect correction and LISI integration scoring, built
on [harmonypy](https://github.com/slowkow/harmonypy) 2.0.2. Harmony is described in
Korsunsky et al., "Fast, sensitive and accurate integration of single-cell data
with Harmony", Nature Methods 16, 1289-1296 (2019),
doi:10.1038/s41592-019-0619-0.

## What this package reproduces

- `run_harmony` runs the upstream `harmonypy.run_harmony` to correct a PCA
  embedding for batch effects and writes the corrected embedding.
- `compute_lisi` runs the upstream `harmonypy.compute_lisi` to score how well a
  neighborhood mixes the categories of a label column.

The package does not reimplement the algorithm. It does not cover upstream
preprocessing (scanpy PCA, gene filtering), UMAP/Leiden downstream steps, or the
C++ build; the harmonypy wheel ships the compiled backend.

## Requirements

- Python 3.10 to 3.14 (validated with 3.12.13), macOS or Linux.
- Prebuilt harmonypy wheels for these platforms; installing from source instead
  needs a C++ compiler, CMake and BLAS.
- Network access is needed once to install the pinned dependencies.

## Install

From the root of this package:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r src/requirements.txt
```

With [uv](https://docs.astral.sh/uv/):

```bash
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python -r src/requirements.txt
```

## Start the server

```bash
python src/harmonypy_mcp.py
```

The server speaks MCP over stdio and waits for a client on stdin. An MCP client
configuration looks like:

```json
{"mcpServers": {"harmonypy": {
  "command": "/path/to/.venv/bin/python",
  "args": ["/path/to/this-package/src/harmonypy_mcp.py"]}}}
```

## Tools

### run_harmony(pcs_path, metadata_path, batch_key, output_path, max_iter_harmony=10, random_state=0, ncores=1)

- `pcs_path`: table of principal components, cells x PCs (TSV or CSV, gzip
  allowed). Numeric columns are used in file order.
- `metadata_path`: table with one row per cell (TSV or CSV, gzip allowed).
- `batch_key`: column in `metadata_path` with batch labels (donor, sample, ...).
- `output_path`: destination TSV for the corrected embedding; parent directories
  are created.
- `max_iter_harmony`, `random_state`, `ncores`: passed to upstream
  `run_harmony`; `ncores=1` keeps results reproducible across machines.
- Returns `n_cells`, `n_pcs`, `n_batches`, `batch_key`, `output_path` and the
  written file under `artifacts`. Unreadable files, a missing batch column, or
  mismatched row counts are rejected with a clear error.
- The input tables are never modified. The output path must be new; an existing
  output or an output path resolving to either input table is rejected.

### compute_lisi(embedding_path, metadata_path, label_key, output_path, perplexity=30)

- `embedding_path`: embedding table, cells x dimensions (TSV or CSV, gzip
  allowed).
- `metadata_path`: table with one row per cell.
- `label_key`: column in `metadata_path` with the labels to score (donor, cell
  type, ...).
- `output_path`: destination TSV for the per-cell LISI values.
- Returns `n_cells`, `n_labels`, `perplexity`, `mean_lisi`, `label_key`,
  `output_path` and the written file under `artifacts`.

## Validation limits

- Verified against the upstream tracked fixtures: 3,500-cell PBMC data
  (minimum per-PC correlation 0.9985 against the R harmony2 reference; threshold
  0.99) and the 400-cell two-dimensional LISI fixture (maximum absolute
  deviation 0.0016 against the tracked reference; tolerance 0.01).
- Dependency and startup checks were run from a fresh environment built only
  from `src/requirements.txt`.
- Not verified: datasets larger than the tracked fixtures, multi-column batch
  correction with mixed metadata types, GPU execution, Windows, and agreement
  with numbers in the paper beyond the tracked reference data.
- Resource use: the tracked 3,500-cell run takes about 0.2 s and tens of MB;
  runtime grows with cells x PCs and the number of batches.

## License

GPL-3.0-or-later; see `LICENSE` and `NOTICE`.
