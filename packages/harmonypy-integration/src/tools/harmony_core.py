"""Harmony and LISI tools for the harmonypy MCP server.

Method: Korsunsky et al., "Fast, sensitive and accurate integration of single-cell
data with Harmony", Nature Methods 16, 1289-1296 (2019),
doi:10.1038/s41592-019-0619-0.

The tools wrap the public API of harmonypy 2.0.2 (GPL-3.0-or-later):
`harmonypy.run_harmony` corrects a PCA embedding for batch effects, and
`harmonypy.compute_lisi` scores local mixing with the Local Inverse Simpson Index.
"""
import pathlib

import numpy as np
import pandas as pd
from fastmcp import FastMCP

import harmonypy

harmony_mcp = FastMCP(
    "harmonypy-core",
    instructions=(
        "Harmony batch-effect correction for PCA embeddings and Local Inverse "
        "Simpson Index (LISI) integration scoring."
    ),
)


def _read_table(path, what):
    """Read a TSV/CSV table, optionally gzip-compressed, with pandas."""
    file_path = pathlib.Path(path).expanduser()
    if not file_path.is_file():
        raise ValueError(f"{what} is not a readable file: {path}")
    suffix = file_path.with_suffix("").suffix if file_path.suffix == ".gz" else file_path.suffix
    separator = "," if suffix == ".csv" else "\t"
    try:
        return pd.read_csv(file_path, sep=separator)
    except Exception as error:
        raise ValueError(f"{what} could not be parsed as a table: {error}") from error


def _require_column(table, column, what):
    if column not in table.columns:
        raise ValueError(
            f"{column!r} is not a column in the {what}; available columns: {list(table.columns)}"
        )
    return table[column]


def _embedding_matrix(table, what):
    """Numeric columns of a table, in file order, as a float64 array."""
    numeric = table.select_dtypes(include=[np.number])
    if numeric.shape[1] == 0:
        raise ValueError(f"{what} has no numeric columns to use as an embedding")
    matrix = np.ascontiguousarray(numeric.to_numpy(dtype=np.float64))
    if not np.isfinite(matrix).all():
        raise ValueError(f"{what} contains non-finite values in the embedding")
    return numeric.columns, matrix


def _check_row_counts(matrix, labels, embedding_what, metadata_what):
    if matrix.shape[0] != labels.shape[0]:
        raise ValueError(
            f"{embedding_what} has {matrix.shape[0]} rows but {metadata_what} has "
            f"{labels.shape[0]} rows; the embedding and the metadata must describe the same cells"
        )


def _prepare_output(output_path, input_paths):
    output = pathlib.Path(output_path).expanduser()
    resolved_output = output.resolve()
    for input_path in input_paths:
        if resolved_output == pathlib.Path(input_path).expanduser().resolve():
            raise ValueError("output_path must be different from the input path; inputs are never overwritten")
    if output.exists():
        raise ValueError(f"output_path already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    return output


def _write_tsv(frame, output_path):
    output = pathlib.Path(output_path).expanduser()
    frame.to_csv(output, sep="\t", index=False)
    return output.resolve()


@harmony_mcp.tool()
def run_harmony(
    pcs_path: str,
    metadata_path: str,
    batch_key: str,
    output_path: str,
    max_iter_harmony: int = 10,
    random_state: int = 0,
    ncores: int = 1,
) -> dict:
    """Run Harmony batch-effect correction on a PCA embedding.

    Args:
        pcs_path: Table of principal components (cells x PCs; TSV or CSV, gzip
            supported). Numeric columns are used in file order.
        metadata_path: Table with one row per cell (TSV or CSV, gzip supported).
        batch_key: Column in the metadata table that holds the batch labels
            (for example the donor, dataset or sample identifier).
        output_path: Where to write the corrected embedding as a TSV table.
        max_iter_harmony: Maximum Harmony iterations (default 10).
        random_state: Random seed for k-means initialization (default 0).
        ncores: BLAS threads; 1 keeps results reproducible across machines.

    Returns:
        Summary with n_cells, n_pcs, n_batches, output_path and the corrected
        embedding under ``artifacts``.
    """
    pcs = _read_table(pcs_path, "pcs_path")
    metadata = _read_table(metadata_path, "metadata_path")
    batch_labels = _require_column(metadata, batch_key, "metadata table")
    columns, matrix = _embedding_matrix(pcs, "pcs_path")
    _check_row_counts(matrix, batch_labels, "pcs_path", "metadata_path")
    output = _prepare_output(output_path, (pcs_path, metadata_path))

    harmony_result = harmonypy.run_harmony(
        matrix,
        metadata,
        batch_key,
        max_iter_harmony=max_iter_harmony,
        random_state=random_state,
        ncores=ncores,
        verbose=False,
    )
    corrected = np.asarray(harmony_result.Z_corr, dtype=np.float64)
    output = _write_tsv(pd.DataFrame(corrected, columns=columns), output)
    return {
        "n_cells": int(corrected.shape[0]),
        "n_pcs": int(corrected.shape[1]),
        "n_batches": int(np.unique(batch_labels.to_numpy()).size),
        "batch_key": batch_key,
        "output_path": str(output),
        "artifacts": [{"path": str(output), "kind": "corrected_pcs_tsv"}],
    }


@harmony_mcp.tool()
def compute_lisi(
    embedding_path: str,
    metadata_path: str,
    label_key: str,
    output_path: str,
    perplexity: float = 30,
) -> dict:
    """Score integration quality with the Local Inverse Simpson Index (LISI).

    LISI is computed for every cell: a value near the number of categories of
    ``label_key`` means the cell's neighborhood mixes all categories, and a
    value near 1 means it is surrounded by one category (Korsunsky et al. 2019).

    Args:
        embedding_path: Embedding table (cells x dimensions; TSV or CSV, gzip
            supported). Numeric columns are used in file order.
        metadata_path: Table with one row per cell (TSV or CSV, gzip supported).
        label_key: Column in the metadata table with the labels to score, for
            example the donor or the cell type.
        output_path: Where to write the per-cell LISI values as a TSV table.
        perplexity: Neighborhood size parameter of the LISI statistic
            (default 30).

    Returns:
        Summary with n_cells, n_labels, mean_lisi, output_path and the per-cell
        values under ``artifacts``.
    """
    embedding = _read_table(embedding_path, "embedding_path")
    metadata = _read_table(metadata_path, "metadata_path")
    labels = _require_column(metadata, label_key, "metadata table")
    _, matrix = _embedding_matrix(embedding, "embedding_path")
    _check_row_counts(matrix, labels, "embedding_path", "metadata_path")
    if perplexity <= 0:
        raise ValueError("perplexity must be greater than 0")
    output = _prepare_output(output_path, (embedding_path, metadata_path))

    lisi = np.asarray(harmonypy.compute_lisi(matrix, metadata, [label_key], perplexity)).ravel()
    output = _write_tsv(pd.DataFrame({f"lisi_{label_key}": lisi}), output)
    return {
        "n_cells": int(lisi.size),
        "n_labels": int(pd.unique(labels).size),
        "perplexity": float(perplexity),
        "mean_lisi": round(float(lisi.mean()), 6),
        "label_key": label_key,
        "output_path": str(output),
        "artifacts": [{"path": str(output), "kind": "lisi_tsv"}],
    }
