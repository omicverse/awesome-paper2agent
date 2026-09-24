"""Measure archive repeatability and line-ending sensitivity without editing packages."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
import zlib
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import catalog


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checks_pass(result: dict) -> bool:
    return result["repeat_equal"] and result["eol_equal"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_id", choices=("scanpy-workflow", "scrublet-doublets", "harmonypy-integration"))
    args = parser.parse_args()
    folder = catalog.ROOT / "packages" / args.package_id
    _, first = catalog.archive(folder, False)
    _, second = catalog.archive(folder, False)
    result = {
        "package_id": args.package_id,
        "python": sys.version,
        "zlib": zlib.ZLIB_RUNTIME_VERSION,
        "repeat_sha256": sha(first),
        "repeat_equal": first == second,
    }
    with tempfile.TemporaryDirectory() as temp:
        variants = {}
        for name, newline in (("lf", "\n"), ("crlf", "\r\n")):
            variant = Path(temp) / name / args.package_id
            shutil.copytree(folder, variant)
            for path in variant.rglob("*"):
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    path.write_text(text.replace("\r\n", "\n").replace("\n", newline), encoding="utf-8", newline="")
            variants[name] = sha(catalog.archive(variant, False)[1])
        result["lf_sha256"] = variants["lf"]
        result["crlf_sha256"] = variants["crlf"]
        result["eol_equal"] = variants["lf"] == variants["crlf"]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if checks_pass(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
