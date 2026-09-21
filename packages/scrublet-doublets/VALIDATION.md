# Validation

## How this was verified

Environment: Python 3.12.12 with `src/requirements.txt` (15 pinned distributions), installed
with `uv pip install -r src/requirements.txt`. All 15 pins resolve on all three indexes the
product may install from — measured, not assumed:

| Index | Result |
|---|---|
| `https://mirrors.aliyun.com/pypi/simple` | 68 packages resolved (15 pinned + transitive), exit 0 |
| `https://pypi.tuna.tsinghua.edu.cn/simple` | 68 packages resolved, exit 0 |
| `https://pypi.org/simple` | 68 packages resolved, exit 0 |

An earlier revision pinned `pandas==3.0.4`; all three dry runs reported it as yanked
("Reported segfaults with datetime-related functionality"), so the pin is `3.0.3` (11 May 2026).
Numba constrains numpy the hardest (`numpy<2.5` at `numba==0.65.1`), which is why numpy is
`2.4.6` and not the newest patch.

Data: a synthetic dataset generated for this check and not shipped with the package — 600 cells
x 300 genes. 570 singlets in three populations of 190, each population expressing its own
10-gene marker block (mean 30 counts) on a background of 270 genes (mean 1.2 counts); plus
**30 planted doublets**, each the sum of the counts of two singlets drawn from *different*
populations (10 per population pair) = exactly 5.00% of cells. The planted identity is stored
in `obs["doublet_truth"]`, which no tool reads except `evaluate_calls`.

The package was started as a real MCP server over stdio (`stdio_client` + `ClientSession` from
`mcp` 1.12.4, running `src/scrublet_doublets_mcp.py` with the pinned environment) and all four
tools were called 15 times in the main run below (9 that must succeed, 6 that must be rejected),
plus 5 further calls on the degenerate inputs described at the end of this section.

| Tool | Result |
|---|---|
| `detect_doublets` | 600 cells x 300 genes, 45 genes left by the gene filter, 12 neighbours, 1200 simulated doublets; automatic threshold 0.1510; 31 cells called (5.17%) against a requested 5%. Detected doublet rate 5.17%, detectable doublet fraction 68.9%, estimated overall rate 7.50%. |
| `evaluate_calls` | against the planted truth: TP 30, FP 1, FN 0, TN 569 — precision 0.968, recall 1.000, F1 0.984, ROC AUC 1.000; median score 0.321 on planted doublets vs 0.048 on the rest. |
| `doublet_threshold_sweep` | 0.10 -> 46 calls; 0.15 -> 31; 0.20 -> 30; 0.30 -> 28; observed-score quantiles p50 0.048, p90 0.090, p95 0.185, p99 0.493. |
| `score_distribution_plot` | wrote a 32 KB PNG; 31 observed cells and 827 of 1200 simulated doublets above the threshold. The PNG was opened and checked: two histograms (observed on log scale, simulated on linear) with the threshold drawn at 0.151. |

Ground truth: the run **recovered the planted doublets**. At the automatic threshold it missed
none and called one singlet (recall 1.000, precision 0.968); at threshold 0.2, taken from the
sweep, a second `detect_doublets` run called exactly 30 cells and `evaluate_calls` returned
TP 30, FP 0, FN 0, TN 570 — precision, recall and F1 all 1.000. That second run also exercises
the explicit-threshold path (`threshold_source` = `explicit`).

Input variants, same detection on each: a dense `X` gave identical numbers (31 calls, threshold
0.1510, TP 30 / FP 1); a file whose `X` holds log-normalised values and whose raw counts sit in
`layers["counts"]` was scored from the layer (`counts_source` = `layers/counts`) with the same
numbers. The raw-count check rejects the log-normalised copy that has no counts layer.

Regression from the published 0.1.0 release: a real 401-cell x 200-gene integer-count
`.h5ad` had 200 genes before Scrublet's filter but only 30 afterwards, so `n_prin_comps=30`
made scikit-learn raise `n_components=30 must be strictly less than min(n_samples, n_features)=30
with svd_solver='arpack'`. The package now translates that specific upstream failure to:

`n_prin_comps=30 is too large for this dataset: 401 cells x 200 genes, and 30 genes remain
after Scrublet's gene filter (min_counts=3, min_cells=3, min_gene_variability_pctl=85); PCA
with svd_solver='arpack' requires n_prin_comps < min(cells, genes_after_filter) = 30. Lower
n_prin_comps to at most 29.`

Reproduce the fixture (run in a temporary directory with the pinned environment):

```python
import anndata as ad
import numpy as np

cells, genes = 401, 200
rng = np.random.default_rng(0)
means = 5 + 45 * np.arange(genes) / (genes - 1)
sigma = 1 + 0.5 * np.sin(np.arange(genes) * 0.17)
rates = means * np.exp(rng.normal(0, sigma, size=(cells, genes)))
adata = ad.AnnData(rng.poisson(rates).astype(np.int32).astype(np.float32))
adata.write_h5ad("fixture.h5ad")
```

Calling `detect_doublets(h5ad_path="fixture.h5ad", output_path="out.h5ad")` through the
package's MCP stdio server reproduces the old failure and returns the translated message after
this change. The same fixture with `n_prin_comps=29` completed (30 retained genes, automatic
threshold 0.1704), and `doublet_threshold_sweep` returns the same translated error at 30. The
zero-variance check still returns upstream's `autodetected range of [nan, nan] is not finite`
unchanged, so only that specific sklearn dimension failure is rewritten. The fixture is
generated by the check, not shipped. No clamp is applied: silently reducing `n_prin_comps`
would change the PCA the caller requested and could alter the scores.

`overall_doublet_rate` was kept under Scrublet's own name after inspecting the pinned source:
`overall_doublet_rate_ = detected_doublet_rate_ / detectable_doublet_fraction_` (scrublet.py),
so it is a ratio of two estimated fractions, not a bounded probability. Values above 1 are
possible and are reported unchanged; USAGE.md now states that reading and the 1.205 value
from the real run is not corrected.

Rejected calls (each returned an MCP error, not a result):

| Call | Error |
|---|---|
| `detect_doublets` on a missing file | `not a readable file: absent.h5ad` |
| `detect_doublets` on log-normalised data | `the expression matrix holds non-integer values; Scrublet needs raw UMI counts, not normalised or log-transformed data` |
| `detect_doublets` with `expected_doublet_rate=0.9` | `expected_doublet_rate must be between 0 and 0.5` |
| `doublet_threshold_sweep` with `thresholds=[1.5]` | `threshold 1.5 is outside (0, 1)` |
| `evaluate_calls` with `truth_key="not_a_column"` | `` `not_a_column` is not a column of obs; name the column holding the truth `` |
| `score_distribution_plot` on a file without a Scrublet run | `this file has no Scrublet run in .uns; run detect_doublets first and plot its output` |

Two failures changed the package while building it, both recorded rather than smoothed over:

1. The first revision passed `sim_doublet_ratio` to `scrub_doublets`; the server answered
   `Scrublet.scrub_doublets() got an unexpected keyword argument 'sim_doublet_ratio'`. At the
   pinned commit the ratio is a constructor argument only, so it is now passed there.
2. Scrublet's default neighbour search is the annoy-backed approximate one. Driving it that way
   gave every cell the identical doublet score (0.0007, observed and simulated alike), because
   the pinned annoy build returned a fixed two-item neighbour list for every query
   (a 100-point, 4-dimensional index answered `get_nns_by_item(i, 30)` with `[0, 1]` for every
   `i`); the automatic threshold then failed and `call_doublets` left `predicted_doublets_` unset.
   The tools therefore pass `use_approx_neighbors=False` (scikit-learn's exact search) and do not
   expose the approximate path.

Two degenerate inputs were also pushed through the server, because neither path appears in a
normal run:

- 60 homogeneous cells (one population, no doublet structure, no planted doublets): the
  automatic threshold did **not** fail — it landed at 0.1377 and called 55 of 60 cells (91.7%),
  an estimated overall doublet rate of 478%. The tools report those numbers rather than
  correcting them, which is the point: the check confirms the package does not manufacture a
  plausible rate. `doublet_threshold_sweep` on the same file returned 0 calls at 0.9, 0.95 and
  0.99. Scrublet's own guidance is to read the score histogram before trusting a threshold.
- 60 byte-identical cells (zero variance, so Scrublet's Fano-factor gene filter divides by zero)
  fail inside upstream: `ValueError: autodetected range of [nan, nan] is not finite`, raised in
  `numpy.histogram` from `scrublet/helper_functions.py get_vscores`. The tool surfaces that error
  unchanged; no guard was added, so a genuine upstream failure is not hidden behind a package
  message. `evaluate_calls` additionally rejects a ground-truth column with a single class:
  `` `all_singlets` has a single class; there is nothing to compare against ``.

Provenance note: the pinned commit `67f8ecbad14e8e1aa9c89b43dac6638cebe38640` (upstream `master`)
carries the code of the released 0.2.3 sdist — `scrublet.py`, `helper_functions.py` and
`__init__.py` are byte-identical to the PyPI sdist (md5 `3e7f4305b04fb7e5f6ef402756bcbd5b`,
`7474af6dc9690a7fb358e3e9e46c52d6`, `1d21b3f0585e398a875259366707618c`) — but that commit's
`setup.py` still says `version = '0.2.2'`; the version string was bumped without a commit. The pin
is the commit the release was built from, not a commit advertising `0.2.3`.

## What remains unverified

- No number in this file is compared against anything reported in the paper. The paper's
  figures, benchmarks and datasets were not reproduced, and no claim is made that this package
  agrees with them.
- Behaviour on real single-cell data. The input is synthetic and free of the ambient RNA, batch
  effects, cell-state continua and within-population doublets that real data contains.
- One size only: 600 cells x 300 genes. Nothing larger was run, and no timing or memory figure
  is claimed beyond the 0.11 s measured for this input.
- One parameter combination (5% expected doublet rate, `sim_doublet_ratio=2.0`,
  `n_prin_comps=30`, `random_state=0`). Other settings were not exercised.
- Multi-sample behaviour, though upstream's own guidance is to run Scrublet per sample.
- The annoy approximate-neighbour path is deliberately not exposed and therefore not verified;
  the dense `X` and `layers["counts"]` paths were each exercised once, on the synthetic input.
- Whether the automatic threshold is sensible on real data. The package does not second-guess
  it: on the structureless 60-cell input it called 91.7% of cells and reported an estimated
  overall rate of 478% without objecting.
- Installation on a machine without a C/C++ compiler: PyPI has no 3.12 wheel for `annoy`, so it
  is built from source (that build succeeded here, on macOS arm64 with Clang 17).
- The `mcp` SDK prints an `IncompleteFieldDefinitionWarning` about a `lifespan` field on stderr
  (from `pydantic_settings`, inside the SDK). It did not affect registration or any call.
- License and redistribution rights, which maintainers confirm during review, and the numerical
  validity of any result on data other than the synthetic input.
