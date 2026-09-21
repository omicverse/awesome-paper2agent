"""Doublet detection with Scrublet, exposed as MCP tools.

Wraps the method of Wolock, Lopez & Klein (Cell Systems, 2019): Scrublet simulates
doublets by adding the counts of random observed transcriptome pairs, embeds observed
and simulated transcriptomes in one manifold, scores each observed cell by how much of
its neighbourhood is simulated, and calls doublets above a score threshold. The tools
call Scrublet's own implementation; nothing of the method is reimplemented here.

Inputs are raw UMI counts in a `.h5ad` file (`layers["counts"]` is used when a
preprocessing pipeline stored them there, otherwise `X`). The nearest-neighbour search
uses the exact scikit-learn path rather than the annoy-backed approximate one: on the
pinned annoy build the approximate index returned a fixed two-item neighbour list, which
made every doublet score identical and left the threshold unset.
"""
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # write PNGs on machines without a display
import matplotlib.pyplot as plt
import anndata as ad
import numpy as np
import scipy.sparse as sp
import scrublet as scr
import sklearn.metrics
from mcp.server.fastmcp import FastMCP

MAX_CELLS = 200_000
MAX_GENES = 60_000
MIN_CELLS = 50
MIN_COUNTS = 3             # upstream scrub_doublets defaults, fixed here
MIN_GENE_CELLS = 3
GENE_VARIABILITY_PCTL = 85
UNS_KEY = "scrublet"

mcp = FastMCP("Scrublet Doublets")


def _in_path(path_str: str, suffix: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_file():
        raise ValueError(f"not a readable file: {path.name}")
    if path.suffix.lower() != suffix:
        raise ValueError(f"expected a {suffix} file")
    return path


def _out_path(path_str: str, suffix: str) -> Path:
    path = Path(path_str).expanduser()
    if path.suffix.lower() != suffix:
        raise ValueError(f"output_path must end in {suffix}")
    return path


def _read_h5ad(path_str: str):
    adata = ad.read_h5ad(_in_path(path_str, ".h5ad"))
    if adata.n_obs > MAX_CELLS or adata.n_vars > MAX_GENES:
        raise ValueError(
            f"dataset too large: {adata.n_obs} cells x {adata.n_vars} genes; "
            f"the limit is {MAX_CELLS} x {MAX_GENES}"
        )
    return adata


def _raw_counts(adata):
    """Raw UMI counts from layers['counts'] when present, else X, plus their origin."""
    source = "layers/counts" if "counts" in adata.layers else "X"
    matrix = adata.layers["counts"] if source == "layers/counts" else adata.X
    if matrix is None:
        raise ValueError("this file has no expression matrix")
    values = np.asarray(matrix.data if sp.issparse(matrix) else matrix).ravel()
    if values.size == 0:
        raise ValueError("the expression matrix is empty")
    if not np.all(np.isfinite(values)):
        raise ValueError("the expression matrix contains non-finite values")
    if values.min() < 0:
        raise ValueError("the expression matrix has negative values; Scrublet needs raw UMI counts")
    if not np.array_equal(values, np.round(values)):
        raise ValueError(
            "the expression matrix holds non-integer values; Scrublet needs raw UMI counts, "
            "not normalised or log-transformed data"
        )
    if adata.n_obs < MIN_CELLS:
        raise ValueError(
            f"Scrublet needs at least {MIN_CELLS} cells to build a neighbour graph; "
            f"this file has {adata.n_obs}"
        )
    return sp.csc_matrix(matrix), source


def _check_parameters(expected_doublet_rate, sim_doublet_ratio, n_prin_comps, threshold):
    if not 0 < expected_doublet_rate < 0.5:
        raise ValueError("expected_doublet_rate must be between 0 and 0.5")
    if not 0 < sim_doublet_ratio <= 10:
        raise ValueError("sim_doublet_ratio must be between 0 and 10")
    if n_prin_comps < 2:
        raise ValueError("n_prin_comps must be at least 2")
    if threshold is not None and not 0 < threshold < 1:
        raise ValueError("threshold must be between 0 and 1, or omitted for the automatic one")


def _run(counts, expected_doublet_rate, sim_doublet_ratio, n_prin_comps, random_state):
    """One Scrublet run; the automatic threshold may or may not have been found."""
    scrub = scr.Scrublet(
        counts,
        expected_doublet_rate=expected_doublet_rate,
        sim_doublet_ratio=sim_doublet_ratio,
        random_state=random_state,
    )
    scores, predicted = scrub.scrub_doublets(
        n_prin_comps=n_prin_comps,
        use_approx_neighbors=False,
        min_counts=MIN_COUNTS,
        min_cells=MIN_GENE_CELLS,
        min_gene_variability_pctl=GENE_VARIABILITY_PCTL,
        verbose=False,
    )
    calls = None if predicted is None else np.asarray(predicted, dtype=bool)
    return scrub, np.asarray(scores, dtype=np.float64), calls


def _explicit_calls(scrub, threshold):
    calls = scrub.call_doublets(threshold=threshold, verbose=False)
    if calls is None:
        raise ValueError("Scrublet returned no calls for the given threshold")
    return np.asarray(calls, dtype=bool)


@mcp.tool()
def detect_doublets(
    h5ad_path: str,
    output_path: str,
    expected_doublet_rate: float = 0.06,
    sim_doublet_ratio: float = 2.0,
    n_prin_comps: int = 30,
    random_state: int = 0,
    threshold: float | None = None,
) -> dict:
    """Score every cell for doublets and call them; writes a new .h5ad with the scores."""
    _check_parameters(expected_doublet_rate, sim_doublet_ratio, n_prin_comps, threshold)
    adata = _read_h5ad(h5ad_path)
    counts, source = _raw_counts(adata)
    out = _out_path(output_path, ".h5ad")
    scrub, scores, calls = _run(
        counts, expected_doublet_rate, sim_doublet_ratio, n_prin_comps, random_state
    )
    if threshold is not None:
        calls = _explicit_calls(scrub, threshold)
    elif calls is None:
        quartiles = np.quantile(np.asarray(scrub.doublet_scores_sim_, dtype=np.float64), [0.1, 0.5, 0.9])
        raise ValueError(
            "Scrublet could not set a threshold automatically: the simulated doublet score "
            f"histogram is not bimodal (its 10th/50th/90th percentile are "
            f"{quartiles[0]:.3f}/{quartiles[1]:.3f}/{quartiles[2]:.3f}). Call again with an "
            "explicit threshold; doublet_threshold_sweep shows what each one would call."
        )
    adata.obs["doublet_score"] = scores
    adata.obs["doublet_z_score"] = np.asarray(scrub.z_scores_, dtype=np.float64)
    adata.obs["predicted_doublet"] = calls
    genes_after_filter = int(np.asarray(scrub._gene_filter).size)
    adata.uns[UNS_KEY] = {
        "expected_doublet_rate": float(expected_doublet_rate),
        "sim_doublet_ratio": float(sim_doublet_ratio),
        "n_prin_comps": int(n_prin_comps),
        "n_neighbors": int(scrub.n_neighbors),
        "random_state": int(random_state),
        "threshold": float(scrub.threshold_),
        "threshold_source": "explicit" if threshold is not None else "automatic",
        "detected_doublet_rate": float(scrub.detected_doublet_rate_),
        "detectable_doublet_fraction": float(scrub.detectable_doublet_fraction_),
        "overall_doublet_rate": float(scrub.overall_doublet_rate_),
        "genes_after_filter": genes_after_filter,
        "counts_source": source,
        "doublet_scores_sim": np.asarray(scrub.doublet_scores_sim_, dtype=np.float64),
        "doublet_errors_obs": np.asarray(scrub.doublet_errors_obs_, dtype=np.float64),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    adata.write_h5ad(out)
    return {
        "cells": int(adata.n_obs),
        "genes": int(adata.n_vars),
        "counts_source": source,
        "genes_after_filter": genes_after_filter,
        "n_neighbors": int(scrub.n_neighbors),
        "simulated_doublets": int(np.asarray(scrub.doublet_scores_sim_).size),
        "threshold": float(scrub.threshold_),
        "threshold_source": "explicit" if threshold is not None else "automatic",
        "predicted_doublets": int(calls.sum()),
        "predicted_doublet_rate": float(calls.mean()),
        "expected_doublet_rate": float(expected_doublet_rate),
        "detected_doublet_rate": float(scrub.detected_doublet_rate_),
        "detectable_doublet_fraction": float(scrub.detectable_doublet_fraction_),
        "overall_doublet_rate": float(scrub.overall_doublet_rate_),
        "score_median": float(np.median(scores)),
        "score_max": float(scores.max()),
        "output": out.name,
    }


@mcp.tool()
def doublet_threshold_sweep(
    h5ad_path: str,
    thresholds: list[float] | None = None,
    expected_doublet_rate: float = 0.06,
    sim_doublet_ratio: float = 2.0,
    n_prin_comps: int = 30,
    random_state: int = 0,
) -> dict:
    """Predicted doublet counts at explicit score thresholds, from one Scrublet run."""
    if thresholds is None:
        thresholds = [round(0.05 * step, 2) for step in range(1, 11)]
    if not 1 <= len(thresholds) <= 20:
        raise ValueError("pass between 1 and 20 thresholds")
    for value in thresholds:
        if not 0 < value < 1:
            raise ValueError(f"threshold {value} is outside (0, 1)")
    _check_parameters(expected_doublet_rate, sim_doublet_ratio, n_prin_comps, None)
    adata = _read_h5ad(h5ad_path)
    counts, source = _raw_counts(adata)
    scrub, scores, automatic = _run(
        counts, expected_doublet_rate, sim_doublet_ratio, n_prin_comps, random_state
    )
    auto_threshold = None if automatic is None else float(scrub.threshold_)
    table = []
    for value in thresholds:
        calls = _explicit_calls(scrub, float(value))
        table.append(
            {
                "threshold": float(value),
                "predicted_doublets": int(calls.sum()),
                "predicted_doublet_rate": float(calls.mean()),
            }
        )
    return {
        "cells": int(adata.n_obs),
        "counts_source": source,
        "auto_threshold": auto_threshold,
        "auto_predicted_doublets": None if automatic is None else int(automatic.sum()),
        "score_quantiles": {
            name: float(np.quantile(scores, q))
            for name, q in (("p50", 0.5), ("p90", 0.9), ("p95", 0.95), ("p99", 0.99))
        },
        "score_max": float(scores.max()),
        "table": table,
    }


@mcp.tool()
def score_distribution_plot(h5ad_path: str, output_path: str, dpi: int = 150) -> dict:
    """Histogram of observed and simulated doublet scores, threshold marked; writes a PNG."""
    if not 50 <= dpi <= 600:
        raise ValueError("dpi must be between 50 and 600")
    adata = _read_h5ad(h5ad_path)
    record = adata.uns.get(UNS_KEY)
    if not record or "doublet_scores_sim" not in record or "threshold" not in record:
        raise ValueError(
            "this file has no Scrublet run in .uns; run detect_doublets first and plot its output"
        )
    if "doublet_score" not in adata.obs:
        raise ValueError("this file has no doublet_score column; run detect_doublets first")
    out = _out_path(output_path, ".png")
    counts, _ = _raw_counts(adata)
    scores = np.asarray(adata.obs["doublet_score"], dtype=np.float64)
    simulated = np.asarray(record["doublet_scores_sim"], dtype=np.float64)
    scrub = scr.Scrublet(counts)  # constructed only to borrow Scrublet's plotting method
    scrub.doublet_scores_obs_ = scores
    scrub.doublet_scores_sim_ = simulated
    scrub.threshold_ = float(record["threshold"])
    figure, _ = scrub.plot_histogram()
    out.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(out, dpi=dpi, bbox_inches="tight")
    plt.close(figure)
    return {
        "cells": int(scores.size),
        "simulated_doublets": int(simulated.size),
        "threshold": float(record["threshold"]),
        "threshold_source": str(record["threshold_source"]),
        "cells_above_threshold": int((scores > scrub.threshold_).sum()),
        "simulated_above_threshold": int((simulated > scrub.threshold_).sum()),
        "score_median": float(np.median(scores)),
        "output": out.name,
    }


@mcp.tool()
def evaluate_calls(
    h5ad_path: str,
    truth_key: str = "doublet_truth",
    score_key: str = "doublet_score",
) -> dict:
    """Compare the called doublets with a ground-truth column; writes nothing."""
    adata = _read_h5ad(h5ad_path)
    if "predicted_doublet" not in adata.obs:
        raise ValueError("this file has no predicted_doublet column; run detect_doublets first")
    if truth_key not in adata.obs:
        raise ValueError(f"`{truth_key}` is not a column of obs; name the column holding the truth")
    if score_key not in adata.obs:
        raise ValueError(f"`{score_key}` is not a column of obs")
    raw = np.asarray(adata.obs[truth_key])
    seen = sorted(set(np.unique(raw).astype(str).tolist()))
    if not set(seen) <= {"True", "False", "0", "1", "0.0", "1.0"}:
        raise ValueError(f"`{truth_key}` must hold booleans or 0/1; it holds {seen[:5]}")
    if len(seen) < 2:
        raise ValueError(f"`{truth_key}` has a single class; there is nothing to compare against")
    truth = np.array([value in ("True", "1", "1.0") for value in raw.astype(str)], dtype=bool)
    called = np.asarray(adata.obs["predicted_doublet"], dtype=bool)
    scores = np.asarray(adata.obs[score_key], dtype=np.float64)
    tp = int((called & truth).sum())
    fp = int((called & ~truth).sum())
    fn = int((~called & truth).sum())
    tn = int((~called & ~truth).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    record = adata.uns.get(UNS_KEY)
    result = {
        "cells": int(truth.size),
        "truth_positive": int(truth.sum()),
        "truth_rate": float(truth.mean()),
        "predicted_positive": int(called.sum()),
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
        "false_discovery_rate": float(fp / (tp + fp)) if tp + fp else 0.0,
        "roc_auc": float(sklearn.metrics.roc_auc_score(truth, scores)),
        "median_score_truth_positive": float(np.median(scores[truth])),
        "median_score_truth_negative": float(np.median(scores[~truth])),
        "threshold": float(record["threshold"]) if record and "threshold" in record else None,
    }
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
