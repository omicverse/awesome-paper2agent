"""Single-cell preprocessing and clustering, exposed as MCP tools.

Follows the workflow the Scanpy paper describes and uses — quality control, filtering,
normalisation and highly variable genes, PCA and a neighbour graph, Leiden clustering,
UMAP and marker genes — calling scanpy's own implementations rather than reimplementing
them. Each tool reads one .h5ad file, writes a new one, and never modifies its input.
"""
from pathlib import Path

import scanpy as sc
from mcp.server.fastmcp import FastMCP

MAX_CELLS = 200_000
MAX_GENES = 60_000
DEFAULT_MITO_PREFIX = "MT-"

mcp = FastMCP("Scanpy Workflow")


def _read(path_str: str):
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise ValueError(f"not a readable file: {path.name}")
    if path.suffix.lower() != ".h5ad":
        raise ValueError("expected an .h5ad file")
    adata = sc.read_h5ad(path)
    if adata.n_obs > MAX_CELLS or adata.n_vars > MAX_GENES:
        raise ValueError(
            f"dataset too large: {adata.n_obs} cells x {adata.n_vars} genes; "
            f"the limit is {MAX_CELLS} x {MAX_GENES}"
        )
    return adata


def _out(path_str: str):
    out = Path(path_str).expanduser()
    if out.suffix.lower() != ".h5ad":
        raise ValueError("output_path must end in .h5ad")
    return out


def _add_qc(adata, mito_prefix: str):
    if not mito_prefix:
        raise ValueError("mito_prefix must not be empty")
    adata.var["mt"] = adata.var_names.str.upper().str.startswith(mito_prefix.upper())
    sc.pp.calculate_qc_metrics(adata, qc_vars=["mt"], percent_top=None, log1p=False, inplace=True)
    return adata


@mcp.tool()
def qc_metrics(h5ad_path: str, mito_prefix: str = DEFAULT_MITO_PREFIX) -> dict:
    """Summarise per-cell QC metrics for one .h5ad file. Writes nothing."""
    adata = _add_qc(_read(h5ad_path), mito_prefix)
    total, genes, pct = (adata.obs[k] for k in ("total_counts", "n_genes_by_counts", "pct_counts_mt"))
    return {
        "n_cells": int(adata.n_obs),
        "n_genes": int(adata.n_vars),
        "n_mito_genes": int(adata.var["mt"].sum()),
        "total_counts": {"min": float(total.min()), "median": float(total.median()), "max": float(total.max())},
        "n_genes_by_counts": {"min": float(genes.min()), "median": float(genes.median()), "max": float(genes.max())},
        "pct_counts_mt": {"median": float(pct.median()), "max": float(pct.max())},
        "cells_above_20pct_mt": int((pct > 20).sum()),
    }


@mcp.tool()
def filter_cells(
    h5ad_path: str,
    output_path: str,
    min_genes: int = 200,
    max_pct_mt: float = 20.0,
    mito_prefix: str = DEFAULT_MITO_PREFIX,
) -> dict:
    """Drop cells with too few detected genes or too much mitochondrial content."""
    if min_genes < 0 or not 0 <= max_pct_mt <= 100:
        raise ValueError("min_genes must be >= 0 and max_pct_mt between 0 and 100")
    out = _out(output_path)
    adata = _add_qc(_read(h5ad_path), mito_prefix)
    before = int(adata.n_obs)
    sc.pp.filter_cells(adata, min_genes=min_genes)
    adata = adata[adata.obs["pct_counts_mt"] <= max_pct_mt].copy()
    if adata.n_obs == 0:
        raise ValueError("every cell was removed; relax min_genes or max_pct_mt")
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)
    return {"cells_before": before, "cells_after": int(adata.n_obs),
            "cells_removed": before - int(adata.n_obs), "output": out.name}


@mcp.tool()
def normalize_hvg(
    h5ad_path: str,
    output_path: str,
    n_top_genes: int = 2000,
    target_sum: float = 10000.0,
) -> dict:
    """Normalise counts per cell, log-transform, and select highly variable genes."""
    if n_top_genes < 10:
        raise ValueError("n_top_genes must be at least 10")
    out = _out(output_path)
    adata = _read(h5ad_path)
    if "counts" not in adata.layers:
        adata.layers["counts"] = adata.X.copy()
    sc.pp.normalize_total(adata, target_sum=target_sum)
    sc.pp.log1p(adata)
    before = int(adata.n_vars)
    sc.pp.highly_variable_genes(adata, n_top_genes=min(n_top_genes, adata.n_vars))
    if int(adata.var["highly_variable"].sum()) == 0:
        raise ValueError("no highly variable genes were selected")
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)
    return {"genes_before": before, "highly_variable_genes": int(adata.var["highly_variable"].sum()),
            "target_sum": target_sum, "output": out.name}


@mcp.tool()
def pca_neighbors(
    h5ad_path: str,
    output_path: str,
    n_pcs: int = 30,
    n_neighbors: int = 15,
    random_state: int = 0,
) -> dict:
    """Scale the data, run PCA, and build the neighbour graph clustering needs."""
    if n_pcs < 2 or n_neighbors < 2:
        raise ValueError("n_pcs and n_neighbors must be at least 2")
    out = _out(output_path)
    adata = _read(h5ad_path)
    sc.pp.scale(adata, max_value=10)
    n_comps = min(n_pcs, max(2, adata.n_vars - 1), max(2, adata.n_obs - 1))
    sc.tl.pca(adata, n_comps=n_comps, svd_solver="arpack", random_state=random_state)
    sc.pp.neighbors(adata, n_neighbors=min(n_neighbors, max(2, adata.n_obs - 1)),
                    n_pcs=n_comps, random_state=random_state)
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)
    variance = adata.uns["pca"]["variance_ratio"]
    return {"n_pcs": int(n_comps), "n_neighbors": int(n_neighbors),
            "variance_ratio_first": float(variance[0]),
            "variance_ratio_sum": float(variance.sum()), "output": out.name}


@mcp.tool()
def leiden_clusters(
    h5ad_path: str,
    output_path: str,
    resolution: float = 1.0,
    random_state: int = 0,
) -> dict:
    """Cluster the neighbour graph with Leiden and record the labels."""
    if resolution <= 0:
        raise ValueError("resolution must be positive")
    out = _out(output_path)
    adata = _read(h5ad_path)
    if "neighbors" not in adata.uns:
        raise ValueError("run pca_neighbors first: this file has no neighbour graph")
    sc.tl.leiden(adata, resolution=resolution, key_added="cluster",
                 random_state=random_state, flavor="igraph", n_iterations=2, directed=False)
    counts = adata.obs["cluster"].value_counts().sort_index()
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)
    return {"n_clusters": int(counts.size), "resolution": resolution,
            "cluster_sizes": {str(k): int(v) for k, v in counts.items()}, "output": out.name}


@mcp.tool()
def umap_embedding(h5ad_path: str, output_path: str, random_state: int = 0, min_dist: float = 0.5) -> dict:
    """Compute a UMAP embedding from the neighbour graph."""
    if not 0 < min_dist <= 1:
        raise ValueError("min_dist must be in (0, 1]")
    out = _out(output_path)
    adata = _read(h5ad_path)
    if "neighbors" not in adata.uns:
        raise ValueError("run pca_neighbors first: this file has no neighbour graph")
    sc.tl.umap(adata, random_state=random_state, min_dist=min_dist)
    embedding = adata.obsm["X_umap"]
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)
    return {"n_cells_embedded": int(embedding.shape[0]), "n_dimensions": int(embedding.shape[1]),
            "spread_x": float(embedding[:, 0].max() - embedding[:, 0].min()),
            "spread_y": float(embedding[:, 1].max() - embedding[:, 1].min()), "output": out.name}


@mcp.tool()
def rank_genes(h5ad_path: str, groupby: str = "cluster", top_n: int = 5) -> dict:
    """Rank genes per group and return the top names, for checking that clusters differ."""
    if top_n < 1 or top_n > 50:
        raise ValueError("top_n must be between 1 and 50")
    adata = _read(h5ad_path)
    if groupby not in adata.obs:
        raise ValueError(f"`{groupby}` is not a column of obs")
    sc.tl.rank_genes_groups(adata, groupby=groupby, method="wilcoxon")
    names = adata.uns["rank_genes_groups"]["names"]
    groups = list(adata.obs[groupby].cat.categories) if hasattr(adata.obs[groupby], "cat") else sorted(set(adata.obs[groupby]))
    return {"groupby": groupby, "n_groups": len(groups),
            "top_genes": {str(g): [str(names[g][i]) for i in range(min(top_n, len(names[g])))] for g in groups}}


if __name__ == "__main__":
    mcp.run(transport="stdio")
