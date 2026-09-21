"""Turn a Paper2MCP delivery into a package this catalog accepts.

The build pipeline in OmicOS already produces the parts a package needs — a single
`src/<repo>_mcp.py` entry point, pinned `src/requirements.txt`, recipient-facing
`USAGE.md`, retained notices — plus the evidence that its tools were exercised:
`reports/expected-mcp-tools.json` (the reconciled tool inventory),
`reports/mcp-acceptance-cases.json` (the calls the verifier ran), and the two strict
real-call reports `reports/mcp-project-environment.json` and
`reports/mcp-clean-environment.json` that the pipeline's own completion gate requires,
together with the extraction-acceptance record `reports/delivery-validation.json`.

This script copies exactly that runtime set into `packages/<package_id>/`, writes the
`metadata.json` the pipeline does not produce, and turns the acceptance cases into the
`VALIDATION.md` this repository requires. It never copies tests, notebooks, reports,
environments or build scratch into the package. A delivery is refused when the pipeline
route keeps non-Python runtime material under `src/` (the R route's
`src/r_scripts/<module>.R`), or when the runtime report / acceptance-case / delivery
evidence is missing, failed or inconsistent: catalog v1 packages are Python servers and
must carry the verification record they advertise.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]

TOOL_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
REPO_URL = re.compile(r'^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?<!\.git)$')
COMMIT = re.compile(r'^[a-f0-9]{40}$')
DOI = re.compile(r'^10\.[0-9]{4,9}/\S*[A-Za-z0-9)]$')
PACKAGE_ID = re.compile(r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')
VERSION = re.compile(r'^[0-9]+\.[0-9]+\.[0-9]+$')
PYTHON = re.compile(r'^3\.[0-9]{1,2}$')
RUNTIME_REPORTS = ('mcp-project-environment.json', 'mcp-clean-environment.json')


def fail(message: str):
    raise SystemExit(f'error: {message}')


def read_json(path: Path, what: str):
    if not path.is_file():
        fail(f'{what} not found: {path}')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        fail(f'{what} is not valid JSON: {path} ({exc})')


def entry_point_file(project: Path) -> Path:
    """The single src/*_mcp.py entry point the pipeline delivers."""
    src = project / 'src'
    if not src.is_dir():
        fail(f'no src/ directory in {project}')
    entry_points = sorted(p for p in src.glob('*_mcp.py') if p.is_file())
    if len(entry_points) != 1:
        fail(f'expected exactly one src/*_mcp.py in {src}, found {len(entry_points)}')
    return entry_points[0]


def check_python_route(project: Path) -> None:
    """Catalog v1 packages are Python; the pipeline's R route keeps src/r_scripts/<module>.R.

    Copying only the Python files silently produced packages that could not run, so a
    delivery with non-Python runtime material under src/ (or a recorded non-Python route)
    is refused instead of converted.
    """
    src = project / 'src'
    requirements = src / 'requirements.txt'
    unexpected = []
    for path in sorted(src.rglob('*')):
        if not path.is_file() or path == requirements or path.suffix == '.py':
            continue
        parts = path.relative_to(src).parts
        if any(part.startswith('.') or part == '__pycache__' for part in parts):
            continue
        unexpected.append(str(path.relative_to(project)))
    if unexpected:
        fail('catalog v1 packages ship Python only, but this delivery keeps non-Python '
             'runtime material under src/: ' + ', '.join(unexpected))
    language = project / '.pipeline/language.json'
    if language.is_file():
        record = read_json(language, 'the pipeline language record')
        route = record.get('route') if isinstance(record, dict) else None
        if route is not None and route != 'python':
            fail(f'the pipeline recorded route {route!r}; catalog v1 packages are Python '
                 'servers and the R/CLI routes cannot be converted by this importer')


def collect_runtime_files(project: Path) -> list:
    """The runtime set only: sources, pinned requirements, USAGE, optional NOTICE."""
    src = project / 'src'
    entry = entry_point_file(project)
    files = [(entry, Path('src') / entry.name)]
    for p in sorted(src.rglob('*.py')):
        if p != entry:
            files.append((p, Path('src') / p.relative_to(src)))
    requirements = src / 'requirements.txt'
    if not requirements.is_file():
        fail(f'no pinned requirements at {requirements}')
    files.append((requirements, Path('src/requirements.txt')))
    for name in ('USAGE.md', 'NOTICE'):
        candidate = project / name
        if candidate.is_file():
            files.append((candidate, Path(name)))
    if not any(dest.name == 'USAGE.md' for _, dest in files):
        fail(f'no USAGE.md in {project}; the pipeline writes a recipient-facing one')
    return files


def check_runtime_evidence(project: Path, entry: Path, tools, cases, python: str) -> dict:
    """Require the strict real-call reports the pipeline's own completion gate requires.

    `reports/mcp-acceptance-cases.json` alone is a plan; the environment reports are the
    evidence that those calls ran in the project environment and in a clean environment
    rebuilt from src/requirements.txt. The VALIDATION.md this importer writes claims
    verifier evidence, so the evidence must exist and match the delivered files.
    """
    cases_by_name = {}
    for case in cases:
        name = case.get('name') if isinstance(case, dict) else None
        if not isinstance(name, str) or name in cases_by_name:
            fail('reports/mcp-acceptance-cases.json: every case needs a unique name')
        cases_by_name[name] = case.get('tool')
    covered = {case['tool'] for case in cases if 'error_contains' not in case}
    missing_positive = [tool for tool in tools if tool not in covered]
    if missing_positive:
        fail('reports/mcp-acceptance-cases.json has no successful acceptance case for: '
             + ', '.join(missing_positive))
    reports = {}
    for name in RUNTIME_REPORTS:
        report = read_json(project / 'reports' / name, f'the pipeline runtime report {name}')
        if not isinstance(report, dict):
            fail(f'reports/{name} must be a JSON object')
        if (report.get('success') is not True or report.get('mode') != 'calls'
                or report.get('require_all_tools') is not True):
            fail(f'reports/{name} is not a successful strict real-call validation '
                 '(success=true, mode="calls", require_all_tools=true)')
        for field in ('expected', 'actual'):
            value = report.get(field)
            if not isinstance(value, list) or set(value) != set(tools):
                fail(f'reports/{name}: {field} does not match reports/expected-mcp-tools.json')
        if report.get('missing') != [] or report.get('unexpected') != []:
            fail(f'reports/{name}: missing or unexpected tools in the validated inventory')
        server = report.get('server')
        if not isinstance(server, str) or not server.strip():
            fail(f'reports/{name}: no validated server path')
        server_path = Path(server)
        if not server_path.is_absolute():
            server_path = project / server_path
        if server_path.resolve() != entry.resolve():
            fail(f'reports/{name}: validated {server!r}, not the delivered entry point')
        if not isinstance(report.get('python'), str) or not report['python'].strip():
            fail(f'reports/{name}: no validated interpreter')
        version = report.get('python_version')
        if not isinstance(version, str) or (version != python and not version.startswith(python + '.')):
            fail(f'reports/{name}: validated with Python {version!r}, not --python {python}')
        outcomes = report.get('cases')
        if not isinstance(outcomes, list) or len(outcomes) != len(cases_by_name):
            fail(f'reports/{name}: case outcomes do not cover the acceptance cases')
        seen = set()
        for outcome in outcomes:
            case_name = outcome.get('name') if isinstance(outcome, dict) else None
            if case_name in seen or case_name not in cases_by_name:
                fail(f'reports/{name}: duplicate or unknown case outcome {case_name!r}')
            if outcome.get('tool') != cases_by_name[case_name] or outcome.get('success') is not True:
                fail(f'reports/{name}: case {case_name!r} is not a successful outcome for its tool')
            seen.add(case_name)
        reports[name] = report
    return reports


def summarize_cases(cases, tools) -> list:
    by_tool = {tool: [] for tool in tools}
    for case in cases:
        tool = case.get('tool') if isinstance(case, dict) else None
        if tool in by_tool:
            by_tool[tool].append(case)
    lines = []
    for tool in tools:
        entries = by_tool[tool]
        if not entries:
            lines.append(f'- `{tool}` — no acceptance case recorded for this tool.')
            continue
        for case in entries:
            name = case.get('name', 'unnamed')
            if 'error_contains' in case:
                lines.append(f'- `{tool}` — case `{name}`: error path, expects `{case["error_contains"]}`.')
                continue
            parts = []
            subset = case.get('expected_subset')
            if subset:
                parts.append(f'asserts {json.dumps(subset, sort_keys=True, separators=(",", ":"))}')
            if case.get('min_artifacts'):
                parts.append(f'requires {case["min_artifacts"]} artifact(s)')
            lines.append(f'- `{tool}` — case `{name}`: ' + ('; '.join(parts) if parts else 'ran'))
    return lines


def build_metadata(args) -> dict:
    if not PYTHON.fullmatch(args.python or ''):
        fail('--python must be the major.minor version the delivery was validated with, '
             f'e.g. 3.12 (the pipeline records the full version); got {args.python!r}')
    for label, value, pattern in (
        ('--package-id', args.package_id, PACKAGE_ID),
        ('--package-version', args.package_version, VERSION),
        ('--repo-url', args.repo_url, REPO_URL),
        ('--commit', args.commit, COMMIT),
    ):
        if not pattern.fullmatch(value or ''):
            fail(f'{label} is not in the expected form: {value!r}')
    if args.doi and not DOI.fullmatch(args.doi):
        fail(f'--doi is not a bare DOI without trailing punctuation: {args.doi!r}')
    if not args.paper_title.strip():
        fail('--paper-title must not be empty')
    if 'pending' in args.license.lower():
        fail('--license must be a real license identifier, not a placeholder')
    return {
        'schema_version': 1,
        'package_id': args.package_id,
        'package_version': args.package_version,
        'name': args.name,
        'summary': args.summary,
        'repo_url': args.repo_url,
        'commit': args.commit,
        'doi': args.doi,
        'paper_title': args.paper_title,
        'tools': args.tools,
        'python': args.python,
        'license': args.license,
        'demo': False,
    }


def build_validation(args, cases, pins: int, delivery_digest) -> str:
    generated = datetime.date.today().isoformat()
    body = [
        '# Validation',
        '',
        f'Generated from a Paper2MCP delivery (`{Path(args.project).name}`) on {generated}.',
        '',
        '## How this was verified',
        '',
        f'- Runtime: Python {args.python}; `src/requirements.txt` pins {pins} distribution(s).',
        f'- Tool inventory reconciled by the build pipeline ({len(args.tools)} tool(s)): '
        + ', '.join(f'`{t}`' for t in args.tools) + '.',
        f'- Strict real-call validation passed in the project environment and in a clean '
        f'environment rebuilt from `src/requirements.txt` ({len(cases)} case(s)).',
        '- Acceptance calls recorded by the pipeline\'s independent verifier:',
    ]
    body += summarize_cases(cases, args.tools)
    if delivery_digest:
        body.append(f'- Delivery report `reports/delivery-validation.json` '
                    f'(sha256 `{delivery_digest}`) records the extracted-archive reinstall '
                    'and the per-tool calls made from it.')
    body += ['', '## What remains unverified', '']
    body += [f'- {item}' for item in args.unverified]
    return '\n'.join(body) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', required=True, type=Path,
                        help='Paper2MCP project root (contains src/, USAGE.md, reports/)')
    parser.add_argument('--package-id', required=True)
    parser.add_argument('--name', required=True)
    parser.add_argument('--summary', required=True)
    parser.add_argument('--package-version', default='0.1.0')
    parser.add_argument('--repo-url', required=True, help='upstream repository the paper uses')
    parser.add_argument('--commit', required=True, help='full 40-character upstream commit')
    parser.add_argument('--doi', default=None)
    parser.add_argument('--paper-title', required=True)
    parser.add_argument('--license', required=True, help='license identifier for this package')
    parser.add_argument('--license-file', required=True, type=Path,
                        help='LICENSE text to place at the package root')
    parser.add_argument('--python', required=True, help="interpreter the pipeline validated with")
    parser.add_argument('--unverified', action='append', default=[],
                        help='one bullet for "What remains unverified"; repeatable and required')
    parser.add_argument('--tool', action='append', default=[],
                        help='override the tool inventory; default is the pipeline report')
    parser.add_argument('--into', type=Path, default=None,
                        help='output root (default: packages/ in this checkout)')
    args = parser.parse_args()

    args.project = args.project.expanduser().resolve()
    if not args.project.is_dir():
        fail(f'--project is not a directory: {args.project}')
    if not args.unverified:
        fail('--unverified is required: state what the pipeline did not check')

    if args.tool:
        args.tools = args.tool
    else:
        inventory = read_json(args.project / 'reports/expected-mcp-tools.json',
                              'the pipeline tool inventory')
        if not isinstance(inventory, list) or not inventory:
            fail('reports/expected-mcp-tools.json must be a nonempty array of tool names')
        args.tools = inventory
    if len(set(args.tools)) != len(args.tools) or not all(
            isinstance(t, str) and TOOL_NAME.fullmatch(t) for t in args.tools):
        fail('tool names must be unique identifiers: ' + repr(args.tools))

    cases = read_json(args.project / 'reports/mcp-acceptance-cases.json',
                      'the pipeline acceptance cases')
    if not isinstance(cases, list) or not cases:
        fail('reports/mcp-acceptance-cases.json must be a nonempty array of cases')

    entry = entry_point_file(args.project)
    check_python_route(args.project)

    metadata = build_metadata(args)
    check_runtime_evidence(args.project, entry, args.tools, cases, args.python)

    license_file = args.license_file.expanduser().resolve()
    if not license_file.is_file():
        fail(f'--license-file not found: {license_file}')

    delivery = args.project / 'reports/delivery-validation.json'
    delivery_report = read_json(delivery, 'the pipeline delivery report')
    if not isinstance(delivery_report, dict) or delivery_report.get('success') is not True:
        fail('reports/delivery-validation.json must record a successful extraction acceptance '
             '(success: true)')
    delivery_digest = hashlib.sha256(delivery.read_bytes()).hexdigest()
    pins = len([line for line in
                (args.project / 'src/requirements.txt').read_text(encoding='utf-8').splitlines()
                if line.strip() and not line.lstrip().startswith('#')])

    target = (args.into or (ROOT / 'packages')) / args.package_id
    if target.exists():
        fail(f'{target} already exists; remove it or pick another package id')

    files = collect_runtime_files(args.project)
    target.mkdir(parents=True)
    for source, dest in files:
        out = target / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, out)
    shutil.copy2(license_file, target / 'LICENSE')
    (target / 'metadata.json').write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    (target / 'VALIDATION.md').write_text(
        build_validation(args, cases, pins, delivery_digest), encoding='utf-8')

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import catalog  # noqa: E402  (same directory; validate() does not depend on ROOT)
    from jsonschema.exceptions import ValidationError  # noqa: E402

    try:
        catalog.validate(target, False)
    except (ValueError, ValidationError) as exc:
        print(f'error: the converted package does not pass validation: {exc}', file=sys.stderr)
        print(f'left {target} in place for inspection', file=sys.stderr)
        return 2

    print(f'wrote {target}')
    print('  files:', ', '.join(sorted(str(p.relative_to(target)) for p in target.rglob('*') if p.is_file())))
    print('  next : python tools/catalog.py validate')
    return 0


if __name__ == '__main__':
    sys.exit(main())
