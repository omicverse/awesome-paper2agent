# Scrublet Doublets

Four tools that run Scrublet on one `.h5ad` file of raw UMI counts: simulate doublets from
random observed transcriptome pairs, embed observed and simulated cells in one manifold,
score every observed cell by how much of its neighbourhood is simulated, and call doublets
above a score threshold. Around the detection itself sit the two checks Scrublet's own
documentation recommends — looking at the score distribution and moving the threshold —
plus a comparison against a known doublet annotation.

Paper: Wolock, Lopez & Klein, *Cell Systems* 8(4):281-291 (2019),
doi:10.1016/j.cels.2018.11.005.
Upstream: `https://github.com/swolock/scrublet` at commit `67f8ecb`.

## Tools

| Tool | Does | Writes |
|---|---|---|
| `detect_doublets(h5ad_path, output_path, expected_doublet_rate=0.06, sim_doublet_ratio=2.0, n_prin_comps=30, random_state=0, threshold=None)` | simulates doublets, scores every cell, calls doublets at the automatic threshold (or at `threshold`) | new `.h5ad` with `obs["doublet_score"]`, `obs["doublet_z_score"]`, `obs["predicted_doublet"]`, plus the run parameters and the simulated scores in `uns["scrublet"]` |
| `doublet_threshold_sweep(h5ad_path, thresholds=None, expected_doublet_rate=0.06, sim_doublet_ratio=2.0, n_prin_comps=30, random_state=0)` | how many cells each explicit score threshold would call (default: 0.05 to 0.50 in steps of 0.05) | nothing |
| `score_distribution_plot(h5ad_path, output_path, dpi=150)` | two-panel histogram of observed and simulated doublet scores with the threshold marked | new `.png` |
| `evaluate_calls(h5ad_path, truth_key="doublet_truth", score_key="doublet_score")` | confusion matrix, precision, recall, F1, false discovery rate and ROC AUC against a ground-truth column | nothing |

The intended order is `detect_doublets` → `score_distribution_plot` and/or
`doublet_threshold_sweep` → `detect_doublets(threshold=...)` if the automatic threshold
looks wrong → `evaluate_calls` when a doublet annotation exists. `score_distribution_plot`
and `evaluate_calls` read the file `detect_doublets` wrote; without it they refuse to run.

## Inputs, outputs and limits

- One `.h5ad` per call. The expression matrix must hold **raw integer UMI counts**: read
  from `layers["counts"]` when a preprocessing pipeline stored them there (the convention of
  the `scanpy-workflow` package in this catalog), otherwise from `X`. Negative values,
  non-integer values (normalised or log-transformed data) and non-finite values are rejected
  rather than silently scored.
- At least 50 cells are required for a neighbour graph; at most 200,000 cells and 60,000 genes.
- The input is never modified. Tools that write take an explicit `output_path`; the others
  return a dictionary and write nothing.
- The nearest-neighbour search uses the exact scikit-learn path. The annoy-backed approximate
  path is not exposed: on the pinned annoy build it returned a fixed two-item neighbour list,
  which gave every cell the same score and left the threshold unset.
- Scrublet scores a sample as a whole, so run it **per sample**, not on merged datasets.
- Scrublet always returns a threshold and calls; on an input with no doublet structure it
  can call most cells (55 of 60 on a structureless check input). Read the score
  distribution before trusting the calls. An input with zero variance between cells fails
  inside upstream's gene filter with `autodetected range of [nan, nan] is not finite`.
- Detection cost grows with cells x simulated doublets; on the 600 x 300 check input one
  detection took 0.11 s.

## Install

```sh
python3.12 -m venv .runtime-venv
.runtime-venv/bin/pip install -r src/requirements.txt
.runtime-venv/bin/python src/scrublet_doublets_mcp.py    # stdio transport
```

Requires Python 3.12 (anndata 0.13.2 needs 3.12 or newer) and a C/C++ compiler: `annoy` is a
hard requirement of Scrublet, and PyPI carries no 3.12 wheel for it, so pip builds it from the
sdist. `src/requirements.txt` pins 15 distributions, all of which resolve on the Aliyun,
Tsinghua and PyPI indexes; installing them needs network access and measured 441 MB of
site-packages. The tools themselves make no network calls. The environment is rebuilt on each
machine and is not distributed with the package.

## What this reproduces, and what it does not

Reproduces: the detection procedure of the paper — doublet simulation by count addition,
joint embedding of observed and simulated transcriptomes, k-nearest-neighbour scoring, and
thresholding — as callable tools, calling Scrublet's own implementation. Verified on a
synthetic input with 30 planted doublets among 600 cells: the calls matched all 30 with no
false positives at threshold 0.2, and 30 of 30 with 1 false positive at the automatic
threshold (see `VALIDATION.md`).

Does not reproduce: any figure, benchmark, dataset or number reported in the paper. The
tools are not compared against the paper's results anywhere, and no claim is made about
their accuracy on real data.
