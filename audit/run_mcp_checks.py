"""Small, reproducible MCP runtime checks for the catalog packages.

This file is an audit harness, not package runtime code. It creates synthetic
inputs outside the packages, starts each entry point over MCP stdio, enumerates
the declared tools, exercises successful and relevant error calls, and writes a
JSON report when --report is supplied.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

from mcp_result_fields import result_field


async def call_tools(entry: Path, calls: list[dict], root: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(entry)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    result = {"entry": str(entry), "calls": []}
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            result["tools"] = sorted(tool.name for tool in listing.tools)
            for item in calls:
                started = time.perf_counter()
                response = await session.call_tool(item["name"], arguments=item.get("arguments", {}))
                elapsed = time.perf_counter() - started
                record = {
                    "name": item["name"],
                    "expect_error": bool(item.get("expect_error")),
                    "is_error": bool(result_field(response, "isError", "is_error")),
                    "elapsed_seconds": round(elapsed, 6),
                }
                content = []
                for block in response.content:
                    if getattr(block, "type", None) == "text":
                        content.append(block.text)
                record["text"] = content
                structured = result_field(response, "structuredContent", "structured_content")
                if structured is not None:
                    record["structured"] = structured
                result["calls"].append(record)
    return result


def write_h5ad(path: Path, *, cells: int = 60, genes: int = 40, with_truth: bool = False) -> None:
    import anndata as ad

    rng = np.random.default_rng(0)
    x = rng.poisson(2.0, size=(cells, genes)).astype(np.float32)
    x[:, :3] = rng.poisson(0.2, size=(cells, 3))
    x[:, 3:6] += rng.poisson(12.0, size=(cells, 3))
    obj = ad.AnnData(x)
    obj.var_names = ["MT-GENE0", "MT-GENE1", "MT-GENE2"] + [f"GENE{i:03d}" for i in range(genes - 3)]
    if with_truth:
        obj.obs["doublet_truth"] = np.array([False] * (cells // 2) + [True] * (cells - cells // 2))
    obj.write_h5ad(path)


def scanpy_calls(root: Path) -> list[dict]:
    source = root / "input.h5ad"
    write_h5ad(source, cells=60, genes=40)
    return [
        {"name": "qc_metrics", "arguments": {"h5ad_path": str(source)}},
        {"name": "filter_cells", "arguments": {"h5ad_path": str(source), "output_path": str(root / "filtered.h5ad"), "min_genes": 1}},
        {"name": "normalize_hvg", "arguments": {"h5ad_path": str(source), "output_path": str(root / "normalized.h5ad"), "n_top_genes": 20}},
        {"name": "pca_neighbors", "arguments": {"h5ad_path": str(root / "normalized.h5ad"), "output_path": str(root / "pca.h5ad"), "n_pcs": 5, "n_neighbors": 100}},
        {"name": "leiden_clusters", "arguments": {"h5ad_path": str(root / "pca.h5ad"), "output_path": str(root / "clustered.h5ad")}},
        {"name": "umap_embedding", "arguments": {"h5ad_path": str(root / "clustered.h5ad"), "output_path": str(root / "umap.h5ad")}},
        {"name": "rank_genes", "arguments": {"h5ad_path": str(root / "clustered.h5ad"), "groupby": "cluster", "top_n": 3}},
        {"name": "filter_cells", "arguments": {"h5ad_path": str(source), "output_path": str(source), "min_genes": 1}, "expect_error": True},
        {"name": "filter_cells", "arguments": {"h5ad_path": str(source), "output_path": str(root / "filtered.h5ad"), "min_genes": 1}, "expect_error": True},
        {"name": "leiden_clusters", "arguments": {"h5ad_path": str(source), "output_path": str(root / "bad.h5ad")}, "expect_error": True},
    ]


def scrublet_calls(root: Path) -> list[dict]:
    source = root / "input.h5ad"
    write_h5ad(source, cells=60, genes=40, with_truth=True)
    return [
        {"name": "detect_doublets", "arguments": {"h5ad_path": str(source), "output_path": str(root / "detected.h5ad"), "n_prin_comps": 4}},
        {"name": "doublet_threshold_sweep", "arguments": {"h5ad_path": str(source), "thresholds": [0.1, 0.2], "n_prin_comps": 4}},
        {"name": "score_distribution_plot", "arguments": {"h5ad_path": str(root / "detected.h5ad"), "output_path": str(root / "scores.png")}},
        {"name": "evaluate_calls", "arguments": {"h5ad_path": str(root / "detected.h5ad")}},
        {"name": "detect_doublets", "arguments": {"h5ad_path": str(source), "output_path": str(source), "n_prin_comps": 4}, "expect_error": True},
        {"name": "detect_doublets", "arguments": {"h5ad_path": str(source), "output_path": str(root / "detected.h5ad"), "expected_doublet_rate": 0.9, "n_prin_comps": 4}, "expect_error": True},
        {"name": "score_distribution_plot", "arguments": {"h5ad_path": str(source), "output_path": str(root / "missing.png")}, "expect_error": True},
    ]


def harmony_calls(root: Path) -> list[dict]:
    import pandas as pd

    rng = np.random.default_rng(0)
    pcs = pd.DataFrame(rng.normal(size=(60, 5)), columns=[f"PC{i}" for i in range(5)])
    metadata = pd.DataFrame({"batch": ["a", "b", "c"] * 20, "label": ["x", "y"] * 30})
    pcs_path = root / "pcs.tsv"
    meta_path = root / "metadata.tsv"
    pcs.to_csv(pcs_path, sep="\t", index=False)
    metadata.to_csv(meta_path, sep="\t", index=False)
    return [
        {"name": "run_harmony", "arguments": {"pcs_path": str(pcs_path), "metadata_path": str(meta_path), "batch_key": "batch", "output_path": str(root / "harmony.tsv"), "ncores": 1}},
        {"name": "compute_lisi", "arguments": {"embedding_path": str(pcs_path), "metadata_path": str(meta_path), "label_key": "label", "output_path": str(root / "lisi.tsv"), "perplexity": 10}},
        {"name": "run_harmony", "arguments": {"pcs_path": str(pcs_path), "metadata_path": str(meta_path), "batch_key": "missing", "output_path": str(root / "missing.tsv"), "ncores": 1}, "expect_error": True},
        {"name": "run_harmony", "arguments": {"pcs_path": str(pcs_path), "metadata_path": str(meta_path), "batch_key": "batch", "output_path": str(pcs_path), "ncores": 1}, "expect_error": True},
        {"name": "compute_lisi", "arguments": {"embedding_path": str(pcs_path), "metadata_path": str(meta_path), "label_key": "label", "output_path": str(root / "lisi.tsv"), "perplexity": 10}, "expect_error": True},
        {"name": "compute_lisi", "arguments": {"embedding_path": str(pcs_path), "metadata_path": str(meta_path), "label_key": "label", "output_path": str(root / "bad.tsv"), "perplexity": 0}, "expect_error": True},
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", choices=("scanpy", "scrublet", "harmony"), required=True)
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    calls = {"scanpy": scanpy_calls, "scrublet": scrublet_calls, "harmony": harmony_calls}[args.package](args.work)
    input_names = ["input.h5ad"] if args.package in ("scanpy", "scrublet") else ["pcs.tsv", "metadata.tsv"]
    before = {name: hashlib.sha256((args.work / name).read_bytes()).hexdigest() for name in input_names}
    result = asyncio.run(call_tools(args.entry.resolve(), calls, args.work))
    result["package"] = args.package
    result["inputs_unchanged"] = before == {
        name: hashlib.sha256((args.work / name).read_bytes()).hexdigest() for name in input_names
    }
    result["expected_error_calls"] = sum(item.get("expect_error", False) for item in calls)
    result["unexpected_error_state"] = [
        {"name": item["name"], "is_error": item["is_error"], "expect_error": item["expect_error"]}
        for item in result["calls"] if item["is_error"] != item["expect_error"]
    ]
    if not result["inputs_unchanged"]:
        result["unexpected_error_state"].append({"inputs_unchanged": False})
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["unexpected_error_state"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
