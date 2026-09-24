"""Run the Scanpy QC tool on Scanpy's fixed public PBMC3k dataset path."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import os
import sys
import time


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


async def call(entry: Path, input_path: Path) -> dict:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = StdioServerParameters(
        command=sys.executable,
        args=[str(entry)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            listing = await session.list_tools()
            start = time.perf_counter()
            response = await session.call_tool("qc_metrics", {"h5ad_path": str(input_path)})
            elapsed = time.perf_counter() - start
            text = next(block.text for block in response.content if getattr(block, "type", None) == "text")
            return {
                "tools": sorted(tool.name for tool in listing.tools),
                "is_error": bool(response.isError),
                "elapsed_seconds": round(elapsed, 6),
                "result": text,
            }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--entry", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    args.work.mkdir(parents=True, exist_ok=True)

    import scanpy as sc

    adata = sc.datasets.pbmc3k()
    input_path = args.work / "pbmc3k.h5ad"
    adata.write_h5ad(input_path)
    result = {
        "source": "scanpy.datasets.pbmc3k() from scanpy==1.12.4",
        "source_summary": "public 10x PBMC3k dataset loaded through Scanpy's dataset helper",
        "h5ad_sha256": sha256(input_path),
        "shape": [int(adata.n_obs), int(adata.n_vars)],
        "mcp": asyncio.run(call(args.entry.resolve(), input_path)),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["mcp"]["is_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
