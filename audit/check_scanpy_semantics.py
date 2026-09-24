"""Compare Scanpy marker ranking with the package's explicit reference call."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

import anndata as ad
import numpy as np
import scanpy as sc


def load_entry(path: Path):
    spec = importlib.util.spec_from_file_location("scanpy_workflow_mcp", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def names(result, groups, top_n):
    values = result.uns["rank_genes_groups"]["names"]
    return {group: [str(values[group][i]) for i in range(top_n)] for group in groups}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(11)
    log_values = rng.normal(size=(24, 6)).astype(np.float32)
    groups = np.array(["a"] * 12 + ["b"] * 12)
    log_values[:12, 0] += 4
    log_values[12:, 1] += 4
    obj = ad.AnnData(log_values.copy())
    obj.obs["cluster"] = groups
    obj.obs["cluster"] = obj.obs["cluster"].astype("category")
    # Deliberately make .raw disagree with the log-normalized layer. A default
    # use_raw=True call must not be allowed to silently change the result.
    obj.raw = ad.AnnData(np.flip(log_values, axis=1), obs=obj.obs.copy(), var=obj.var.copy())
    obj.layers["log_normalized"] = log_values.copy()
    obj.X = rng.normal(size=obj.shape).astype(np.float32)
    path = args.work / "marker-input.h5ad"
    obj.write_h5ad(path)

    module = load_entry(args.entry)
    observed = module.rank_genes(str(path), groupby="cluster", top_n=3)

    reference = ad.read_h5ad(path)
    sc.tl.rank_genes_groups(reference, groupby="cluster", method="wilcoxon",
                            layer="log_normalized", use_raw=False)
    expected = names(reference, list(reference.obs["cluster"].cat.categories), 3)
    same_as_explicit = observed["top_genes"] == expected

    hidden_raw = ad.read_h5ad(path)
    sc.tl.rank_genes_groups(hidden_raw, groupby="cluster", method="wilcoxon", use_raw=True)
    raw_names = names(hidden_raw, list(hidden_raw.obs["cluster"].cat.categories), 3)
    result = {
        "observed": observed["top_genes"],
        "explicit_reference": expected,
        "implicit_raw_reference": raw_names,
        "matches_explicit_reference": same_as_explicit,
        "raw_would_differ": raw_names != expected,
        "log_layer_preserved": "log_normalized" in ad.read_h5ad(path).layers,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if same_as_explicit and result["raw_would_differ"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
