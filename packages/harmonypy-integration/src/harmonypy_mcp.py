"""MCP server exposing the harmonypy Harmony implementation.

harmonypy implements Harmony from Korsunsky et al., "Fast, sensitive and accurate
integration of single-cell data with Harmony", Nature Methods 16, 1289-1296
(2019), doi:10.1038/s41592-019-0619-0. Upstream code:
https://github.com/slowkow/harmonypy at commit dbd0984 (v2.0.2), GPL-3.0-or-later.

Tools: run_harmony (batch-effect correction of a PCA embedding) and compute_lisi
(Local Inverse Simpson Index integration scoring).
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from fastmcp import FastMCP  # noqa: E402  (path setup must run first)

from tools.harmony_core import harmony_mcp  # noqa: E402

mcp = FastMCP(
    "harmonypy",
    instructions="Run Harmony batch-effect correction and LISI integration scoring.",
)
mcp.mount(harmony_mcp)

if __name__ == "__main__":
    mcp.run(transport="stdio")
