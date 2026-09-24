# Local audit harness

These scripts are local acceptance checks for the three catalog packages. They
are intentionally outside `packages/` and are not copied into release ZIPs.
They create all inputs under a caller-supplied temporary directory, start each
entry point over MCP stdio, enumerate tools, call every declared tool on a small
input, and include relevant rejection calls. The harness returns non-zero when
an expected success/error state differs.

`audit/BASELINE.md` records the original Windows audit at commit
`3315ce749cff8cc935ff966586296f21ca0a5819`; it is historical evidence,
not the current pull-request head. The commands below target the current
Scanpy 0.1.2 and Scrublet 0.1.2 package versions. Rebuild each Python 3.12
environment from that package's pinned `src/requirements.txt` before running
them. The harness accepts both the SDK's camelCase result fields and FastMCP
4's snake_case result fields.

Example Windows commands from the repository root:

```powershell
$audit = "$env:TEMP\paper2agent-audit-pr11"
$scan = "$env:TEMP\paper2agent-envs\scanpy-0.1.2\Scripts\python.exe"
& $scan audit/run_mcp_checks.py --package scanpy `
  --entry packages/scanpy-workflow/src/scanpy_workflow_mcp.py `
  --work "$audit\scanpy" --report "$audit\scanpy.json"

$scrub = "$env:TEMP\paper2agent-envs\scrublet-0.1.2\Scripts\python.exe"
& $scrub audit/run_mcp_checks.py --package scrublet `
  --entry packages/scrublet-doublets/src/scrublet_doublets_mcp.py `
  --work "$audit\scrublet" --report "$audit\scrublet.json"

& $scan audit/check_scanpy_semantics.py `
  --entry packages/scanpy-workflow/src/scanpy_workflow_mcp.py `
  --work "$audit\scanpy-semantics"
```

The harness records per-call wall time and the JSON reports retain the tool
enumeration, structured/text results and expected-error states. Native peak
RSS is not reported by these scripts because neither declared runtime pins
`psutil`; resource limits were therefore applied by dataset size and wall-time
observation, with any unrun larger size called out in the final report.
