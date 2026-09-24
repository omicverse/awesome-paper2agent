"""Bounded Scanpy input-size and sparse/dense consistency check."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time

import anndata as ad
import numpy as np
import scipy.sparse as sp


def make_input(path: Path, cells: int, genes: int, density: float, dense: bool = False) -> None:
    rng = np.random.default_rng(20260921 + cells + genes)
    nnz = max(1, int(cells * genes * density))
    rows = rng.integers(0, cells, size=nnz)
    cols = rng.integers(0, genes, size=nnz)
    values = rng.poisson(2.0, size=nnz).astype(np.float32) + 1
    matrix = sp.coo_matrix((values, (rows, cols)), shape=(cells, genes)).tocsr()
    if dense:
        matrix = matrix.toarray()
    obj = ad.AnnData(matrix)
    obj.var_names = ["MT-GENE0", "MT-GENE1", "MT-GENE2"] + [f"GENE{i:05d}" for i in range(genes - 3)]
    obj.write_h5ad(path)


async def run(entry: Path, paths: list[Path]) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(command=sys.executable, args=[str(entry)])
    records = []
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for path in paths:
                start = time.perf_counter()
                result = await session.call_tool("qc_metrics", {"h5ad_path": str(path)})
                elapsed = time.perf_counter() - start
                text = next(block.text for block in result.content if getattr(block, "type", None) == "text")
                records.append({"path": str(path), "elapsed_seconds": round(elapsed, 6), "is_error": bool(result.isError), "text": text})
    return records


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)
    paths = []
    for cells, genes, density in ((600, 300, 0.03), (5000, 2000, 0.003), (20000, 5000, 0.0005)):
        path = args.work / f"sparse-{cells}x{genes}.h5ad"
        make_input(path, cells, genes, density)
        paths.append(path)
    dense = args.work / "dense-600x300.h5ad"
    make_input(dense, 600, 300, 0.03, dense=True)
    records = asyncio.run(run(args.entry.resolve(), [paths[0], dense, paths[1], paths[2]]))
    result = {"records": records, "peak_memory_measured": False, "note": "QC/read path only; larger full workflows were not run."}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if any(item["is_error"] for item in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
