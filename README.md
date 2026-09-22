# awesome-paper2agent

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE) [![Metadata schema v1](https://img.shields.io/badge/metadata-schema%20v1-blue.svg)](schema/package-v1.schema.json) [![Contributions: PR](https://img.shields.io/badge/contributions-PR-orange.svg)](CONTRIBUTING.md)

🇨🇳 **中文版**: [README.zh.md](README.zh.md)

A paper's value extends beyond being read, yet its methods are often dispersed across code, notebooks and supplementary material. Making use of them requires understanding the implementation, aligning dependencies and running the workflow end to end. That is a barrier to use, and it also means the same packaging gets repeated by different people.

We built the Paper2Agent community to collect finished paper MCP packages in one place; once installed in [OmicOS](https://omicos.cn/), a paper's methods become callable in the conversation like any built-in tool.

---

## Quick start

Paste this into any agent, attach the paper, and it will build a package that passes validation
and is ready to open as a pull request:

```text
Follow AGENTS.md in this repository to build a package from the attached paper PDF,
using <upstream repository URL>. Focus on <methods or figures to expose as tools>.
```

Build instructions for agents live in [AGENTS.md](AGENTS.md) — one file, one source of truth.

## How a package reaches users

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/assets/flow-en-dark.svg">
  <img src="docs/assets/flow-en-light.svg" width="677"
       alt="Source → Pull request → Static checks → Maintainer review → ZIP + index → MCP client">
</picture>

Every published package records the paper it comes from, the upstream repository, the exact commit,
its license, and the checksum of the artifact. `reviews.json` pins maintainer approvals to package
content, so any change to code or metadata requires a new review. A package stays out of the release
index until it is approved, and the default branch refuses to release while an unapproved package
sits under `packages/`.

## What is in this repository

| Path | Purpose |
|---|---|
| `packages/<package_id>/` | Submitted packages; published once approved |
| `examples/sequence-stats/` | A synthetic example that exercises the full catalog pipeline |
| `schema/package-v1.schema.json` | Versioned metadata contract for submissions |
| `tools/catalog.py` | Validates and deterministically packages sources |
| `tools/from_paper2mcp.py` | Converts an OmicOS build-pipeline delivery into a package |
| `docs/contract.md` | Consumer contract, artifact layout and limitations |
| `AGENTS.md` | Build instructions for agents |

<details>
<summary>Package layout</summary>

```text
packages/<package_id>/
  metadata.json          strict schema; no fields outside it
  USAGE.md               purpose, inputs, outputs, limits, install steps
  VALIDATION.md          how each tool was verified, and what was not
  LICENSE                license of this submission
  NOTICE                 optional upstream notices
  src/requirements.txt   exactly pinned dependencies (package==version)
  src/<name>_mcp.py      the single MCP entry point
```

</details>

## Local checks

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/catalog.py validate
python tools/catalog.py demo          # local example catalog in dist/demo/
python tools/catalog.py build --revision $(git rev-parse HEAD)
```

`validate --require-approved` additionally fails while a package under `packages/` has no matching
entry in `reviews.json`. The required `release-gate` check runs it on pull requests and the default
branch. A release build requires a committed, clean source revision.

## Demo runtime check

`requirements-dev.txt` covers validation only. The example server needs its own environment:

```bash
python3.11 -m venv .runtime-venv
.runtime-venv/bin/pip install -r examples/sequence-stats/src/requirements.txt
PYTHONDONTWRITEBYTECODE=1 .runtime-venv/bin/python tools/smoke_example.py
```

## Licensing

The repository's own contents — schema, validator, workflows and documentation — are Apache-2.0;
see [LICENSE](LICENSE). A submission is **not** relicensed by it: `packages/<package_id>/LICENSE`
stays authoritative for that package, and the declared license must permit redistribution of
everything it contains. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Contributing

Contributions are submitted as pull requests and reviewed before merge. Merging code and approving
content are separate: an approval in `reviews.json` is required before a package is published.
