# Public contract v1

Metadata is strict and allowlisted. `repo_url` + full `commit` identify upstream code;
`package_id` + `package_version` identify wrapper revisions. Publishing records the catalog source revision separately.
`tools` is a declared list, not a runtime verification claim. `repo_url` is a canonical
`https://github.com/owner/repo` URL without a `.git` suffix; `doi` may be null when the paper has
none, while `paper_title` is required for every published package.

ZIP: one top-level package directory containing `metadata.json`, `USAGE.md`, `VALIDATION.md`,
`LICENSE`, an optional `NOTICE`, exactly one `src/*_mcp.py`, `src/requirements.txt`, and source
modules. `VALIDATION.md` records how the tools were exercised and what was left unchecked; it is
required for published packages and is checked for its two section headings and for a mention of
every declared tool. Its content is judged by a human, not by the validator. `metadata.json` ships
inside the archive as well as in the index, so a ZIP circulating on its own still carries the paper,
commit, declared license and tool list it was built from.
Limits: 20 MiB ZIP, 100 MiB expanded, 2000 files. Rejected by validation: absolute local paths,
`file://` URIs, loopback and wildcard-bind hosts (`localhost`, `127.0.0.1`, `0.0.0.0`, `::1`),
private and link-local addresses, cloud metadata endpoints, credential-shaped strings, dependency
URLs and unsafe archive paths. Public hostnames are unaffected. `src/` accepts Python modules only;
larger data assets are not distributed through this catalog in v1.

Index: `{schema_version: 1, source_revision, channel, packages: [...]}`. Each package combines validated metadata
and generated `artifact: {path, sha256, bytes}`. `path` is a relative release-asset filename, not a URL.
Resolve assets against a trusted release base. Pin the selected release and verify size/hash before installation.
Hash verification confirms bytes, NOT safety. Dependency installation and MCP startup execute code and require approval.

Platform scope: v1 accepts public `github.com` repositories only. GitLab, institutional forges and SSH
remotes are out of scope; extend the schema deliberately if that changes.

Version policy: `packages/<package_id>/` holds one working revision per package, and a release index
carries one version of each `package_id`. Bumping `package_version` replaces the previous entry in the
index rather than keeping both; history lives in git tags and releases, not in the index.

Installation is explicit and separate from public contribution. Installing a community package never
implies lab sharing, and public sourcing never implies an environment is distributed with it.

A catalog built with `channel: local-demo` contains examples only and is not a release. Release builds
exclude `examples/`, require a clean committed tree and reject missing upstream provenance.

## What validation does not establish

- `tools` is what the package declares. Nothing runs the entry point to check that the
  advertised tools exist, so a reviewer either does that by hand or accepts the declaration.
- `name`, `summary`, `tools` and `USAGE.md` are contributor text that reaches a user's agent
  context once installed. They are reviewed but not filtered; treat a package's own prose as
  untrusted input.
- A review says a maintainer looked at the content. It is not a security audit, and it is not
  evidence that the package's numbers agree with the paper.
- `repository` URLs and commits are checked to exist on GitHub, not to contain what the package
  claims to wrap.

## Reviewed catalog

`reviews.json` is a maintainer-owned approval ledger, not a contributor-supplied badge.
Each approval pins package ID/version, `submitted_by` (the GitHub login that opened the pull request the
package arrived in), `reviewed_by` (the maintainer recording the approval), the review date, and
`content_sha256` computed over canonical JSON metadata (`sort_keys=True`, compact separators, Python
default ensure_ascii) + one newline byte + the deterministic ZIP bytes. Submission and review are separate
claims: `submitted_by` attributes who sent the package, `reviewed_by` attributes who accepted it.
Changing metadata OR code invalidates approval.
No approval is inferred from passing tests, and approvals are recorded only for content that has been
reviewed. Protect this file with maintainer review before publication.

`python tools/catalog.py reviewed` creates a local reviewed-only index. `validate --require-approved`
fails while any package under `packages/` lacks a matching approval; the required `release-gate`
check runs it on pull requests and the default branch. `build` requires the same. `reviewed-local`
is a local inspection channel, not a release.
An approval means maintainer content review, not security certification or numerical reproduction.
The release index inherits trust from its controlled publisher; an untrusted index cannot
establish review merely by containing `review` fields.

During pull-request validation, a package whose content or version no longer matches an old approval
is reported as pending review by the ordinary `validate` check so its static contents can still be
checked. The separate `release-gate` check stays red until a maintainer approves that exact content
in the same pull request; reviewed exports and release builds remain strict too.

## Licensing

Repository contents — schema, validator, workflows and documentation — are Apache-2.0. Each package
keeps the license declared in its own `LICENSE`; the catalog aggregates packages, it does not relicense
them. Consumers redistributing a package must follow that package's license.
