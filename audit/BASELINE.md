# Baseline and post-fix evidence

Fixed source: `3315ce749cff8cc935ff966586296f21ca0a5819` (`main`, 2026-09-21).
The baseline below was run before edits in the independent worktree, and the
post-fix checks were rerun after the package versions and catalog archive logic
changed. `reviews.json` was not edited.

## Baseline failures reproduced

- `python -m unittest discover -s tests -v`: 69 tests, 1 failure and 19 errors
  under the Windows GBK default encoding. The errors came from test-side
  `Path.read_text()` calls without `encoding="utf-8"`; the remaining failures
  were a Windows symlink cleanup error and a `/`-specific R-route assertion.
- `python -X utf8 -m unittest discover -s tests -v`: 69 tests, 2 failures and
  1 error. The locale errors disappeared, leaving the symlink cleanup,
  separator assertion, and the checkout's stale approval digest failure.
- The baseline `catalog.py` computed different content digests from every
  approval in `reviews.json`:

  | package | CRLF checkout digest | LF checkout digest | ledger digest |
  |---|---|---|---|
  | `harmonypy-integration` | `c2a67c66b8ea42b8cea49154c3915d4a3114fd73b4847dde7e591894fc7f6354` | `32728d11e6921fe9052d83845c021d4e0c63d9b73311b9bdd45652d6761b3b25` | `5d751f2e4e70a0b000be2c48d9f23e54052d07dea143abb7c50fc3c2da7cd1f9` |
  | `scanpy-workflow` | `4397f2fbb11fde8804cc163dc0b4505171ec455d849728da2da9c3dc3aebe01b` | `407485672cf7d298370a035ddf92fa1adfc551758392e7e45e6f1e6617dd6986` | `ecc672e9da1ee0a10fd504d5e9a648cea77051a97d539ff27479fb32f3a1d6c2` |
  | `scrublet-doublets` | `d29d88c3ecfc326dc51d5c7f26159855cf9d1644fad04318063b83436880c6fe` | `a96e17be8da39afa29037852d3198e66d0cc5ada22c9716e7b5ece1570980f64` | `4d2471c8ecf23be978bb215f402d31eea45a0f56d606702030b50c38df6711fc` |

- Baseline Scanpy MCP calls showed `pca_neighbors(n_neighbors=100)` on 60
  cells returned `n_neighbors: 100`, although Scanpy actually clipped the
  graph to 59. Its output helpers also accepted output equal to the input and
  existing output paths.
- Baseline Scrublet MCP calls wrote to the input when `output_path ==
  h5ad_path`, and silently replaced an existing output. The accidental input
  overwrite also changed the later “no prior Scrublet run” error case into a
  success, demonstrating why the protection is important.

## Post-fix results

- Repository tests: 70/70 passed both with the Windows default encoding and
  with `-X utf8`.
- Direct package validation: all three package directories passed the static
  validator. The demo catalog build passed.
- ZIP repeatability: repeated archives are byte-identical; after the catalog
  fix, LF and CRLF copies produce the same digest. Python 3.10.20 and 3.12.13
  both used zlib 1.3.1 and produced the same tested digest.
- Scanpy and Scrublet were each rebuilt from the new ZIP, extracted, and run
  through MCP. Every declared tool was enumerated; all success and rejection
  cases matched expectations; the generated input SHA256 stayed unchanged.
- The Scanpy semantic check matched an explicit same-version upstream call
  using `layer="log_normalized", use_raw=False`, while an intentionally
  conflicting implicit `.raw` call produced different marker names.
- Public-data path: `scanpy.datasets.pbmc3k()` under Scanpy 1.12.4 produced
  2,700 x 32,738 cells/genes, was written as H5AD with SHA256
  `0a485d8cd25151db0d6c24d4e051372b4ceba32fb409dd112754aac1f40bb2c0`, and
  passed the repaired MCP `qc_metrics` tool (13 mitochondrial genes; median
  2,197 total counts; one cell above 20% mitochondrial counts).

At the original Windows post-fix stage, the release gate remained intentionally
blocked because the three package versions/content changed and `reviews.json`
still contained the old maintainer approvals. That was the required “tested
locally, pending maintainer review” state, not an approval workaround.

## Maintainer follow-up on the PR branch (2026-09-23)

- On macOS arm64, Python 3.12.13 environments were rebuilt from each package's
  pinned requirements. The three packages were archived, extracted and run over
  MCP stdio with credentials removed from the process environment.
- Scanpy exposed 7 tools and passed 10 success/rejection calls; Scrublet exposed
  4 tools and passed 7; harmonypy exposed 2 tools and passed 6. All source inputs
  retained their SHA256 values. The Harmony run required a fix to the audit
  harness for FastMCP 4's snake_case result fields; that fix is included in
  this PR.
- The 78 repository unit tests, ordinary catalog validation, three LF/CRLF
  archive comparisons, and the explicit Scanpy marker-layer reference check
  passed locally. Strict approval validation first rejected the three stale
  approvals as expected, then passed after the maintainer recorded final
  content hashes in `reviews.json` on this PR branch.
- These bounded checks do not reproduce paper figures, establish numerical
  agreement with the paper, or test OmicOS installation end to end.
