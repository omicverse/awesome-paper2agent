# Scanpy Workflow

Seven tools that carry one .h5ad file through the workflow the Scanpy paper describes:
quality control, filtering, normalisation with highly variable genes, PCA and a neighbour
graph, Leiden clustering, a UMAP embedding, and marker genes per cluster.

Paper: Wolf, Angerer & Theis, *Genome Biology* 19, 15 (2018), doi:10.1186/s13059-017-1382-0.

## Tools

| Tool | Does | Writes |
|---|---|---|
| `qc_metrics(h5ad_path, mito_prefix="MT-")` | per-cell QC summary, mitochondrial content included | nothing |
| `filter_cells(h5ad_path, output_path, min_genes=200, max_pct_mt=20, mito_prefix="MT-")` | drops cells with too few genes or too much mitochondrial read content | new `.h5ad` |
| `normalize_hvg(h5ad_path, output_path, n_top_genes=2000, target_sum=10000)` | keeps raw counts in `layers["counts"]`, normalises, log-transforms, selects highly variable genes | new `.h5ad` |
| `pca_neighbors(h5ad_path, output_path, n_pcs=30, n_neighbors=15, random_state=0)` | scales, runs PCA, builds the neighbour graph | new `.h5ad` |
| `leiden_clusters(h5ad_path, output_path, resolution=1.0, random_state=0)` | Leiden labels in `obs["cluster"]` | new `.h5ad` |
| `umap_embedding(h5ad_path, output_path, random_state=0, min_dist=0.5)` | UMAP coordinates in `obsm["X_umap"]` | new `.h5ad` |
| `rank_genes(h5ad_path, groupby="cluster", top_n=5)` | top genes per group, Wilcoxon | nothing |

They are meant to be called in that order: `pca_neighbors` before `leiden_clusters` and
`umap_embedding` (both refuse a file with no neighbour graph), and `rank_genes` on the
clustered file. Every tool reads one file and writes a new one; the input is never modified.
Output paths must be new `.h5ad` files; an existing output or an output path that resolves
to the input is rejected rather than overwritten. `normalize_hvg` keeps its log-normalized
matrix in `layers["log_normalized"]`; `rank_genes` uses that layer explicitly when it is
present and never implicitly selects `.raw`.

## Inputs, limits, and what it does not do

- One `.h5ad` file per call, from the local filesystem. At most 200,000 cells and 60,000 genes;
  larger inputs are rejected rather than truncated.
- `mito_prefix` defaults to `MT-` (human). Mouse data uses `mt-`; check it before reading QC numbers.
- No batch processing, no remote or object-storage input, no plotting.
- Doublet detection — part of the paper's Scanpy tool list — is **not** exposed here.

## Install

```sh
python3.12 -m venv .runtime-venv
.runtime-venv/bin/pip install -r src/requirements.txt
.runtime-venv/bin/python src/scanpy_workflow_mcp.py     # stdio transport
```

Requirements are pinned and fetched from a package index, so installation needs network
access; the tools themselves do not. Expect several hundred MB of site-packages (Scanpy,
Leidenalg and UMAP bring compiled dependencies) and a few minutes to install. The
environment is rebuilt on each machine and is not distributed with the package.

## What this reproduces, and what it does not

Reproduces: the preprocessing and clustering workflow as callable tools, verified on data
with three planted populations — the tools recovered exactly those three (see `VALIDATION.md`).

Does not reproduce: the paper's figures, benchmarks or published datasets; no claim is made
about agreement with numbers reported in the paper. Only `.h5ad` inputs are supported.
