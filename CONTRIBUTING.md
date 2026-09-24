# Contributing

Submissions are made as pull requests and reviewed before merge.

## Package requirements

1. Provide reviewable source: `metadata.json`, `USAGE.md`, `VALIDATION.md`, `LICENSE`, and a pinned
   `src/requirements.txt`.
2. Declare the upstream public repository and its full commit. The wrapper repository's commit is not the upstream commit.
3. State what the package reproduces and what it does not, together with required inputs, network access and expected resource use.
4. Include only content you hold the rights to redistribute, and preserve upstream notices and third-party licenses.
5. Keep every file within its intended public scope. The validator rejects credentials, private network addresses,
   local paths, logs, environments and real user data before review begins.
6. Record in `VALIDATION.md` how each tool was exercised and what remains unchecked. A delivery
   from the OmicOS build pipeline can generate this file — see `tools/from_paper2mcp.py`.
7. Run validation and tests locally. Static checks reduce risk; they do not certify the absence of secrets or malicious code.

## Licensing of a submission

The repository's own contents are Apache-2.0. **A submitted package is not relicensed by that**:
`packages/<package_id>/LICENSE` stays authoritative, and the declared license must permit redistribution of
everything in the package. If the package wraps or derives from upstream code, keep the upstream notices and
make sure the declared license is compatible with the upstream one. Maintainers confirm this during review;
the validator can only check that a license file exists and is not a placeholder.

## Review

Every pull request runs the static checks with read-only workflow permissions. Runtime verification is performed
separately in an isolated environment with no credentials. Changes to workflows, schema or validator logic
receive dedicated maintainer review. Submitted package code is never executed in workflows with privileged access.

Merging the code and approving the content are separate steps. A package stays out of the release index until a
maintainer records an approval for its exact content in `reviews.json`.

The `validate` check reports static defects even while a package is awaiting review. The separate,
required `release-gate` check runs on pull requests and pushes and requires every package under
`packages/` to carry an approval for its current content. A maintainer adds that approval entry
**inside the same pull request** before merging it: open the PR, review the content, append the
`reviews.json` entry with the content hash, let CI re-run, then merge. Adding the package first and
the approval afterwards would leave `main` failing its own gate.

The maintainer writing that entry records both identities: `submitted_by` is the GitHub account that opened the
pull request supplying the exact package version/content being approved (including an update PR), while
`reviewed_by` is the maintainer recording the approval. They answer different questions, so consumers can
display the submitter next to the reviewer; the validator refuses a blank or non-login `submitted_by`.

## Building with an agent

Build instructions for agents live in [AGENTS.md](./AGENTS.md); the README shows the one-line prompt
that points an agent at it. The agent prepares the files and a verification report; a human still
confirms licensing, redistribution rights and numerical validity.
