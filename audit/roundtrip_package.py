"""Archive a catalog package and extract it for standalone runtime checks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("package_id", choices=("scanpy-workflow", "scrublet-doublets", "harmonypy-integration"))
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    folder = catalog.ROOT / "packages" / args.package_id
    meta, data = catalog.archive(folder, False)
    args.work.mkdir(parents=True, exist_ok=True)
    archive = args.work / f"{args.package_id}-{meta['package_version']}.zip"
    archive.write_bytes(data)
    extracted = args.work / "extracted"
    extracted.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extracted)
        names = zf.namelist()
    result = {
        "package_id": args.package_id,
        "package_version": meta["package_version"],
        "zip": str(archive),
        "zip_sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "members": names,
        "entry": str(extracted / args.package_id / "src"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
