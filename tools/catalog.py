"""Static-only public catalog validator/packager. Never executes submitted sources."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'schema/package-v1.schema.json').read_text(encoding='utf-8'))

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_FILES = 2000
MAX_ZIP_BYTES = 20 * 1024 * 1024

ALLOWED_FILES = {
    'metadata.json', 'USAGE.md', 'LICENSE', 'NOTICE', 'VALIDATION.md', 'src/requirements.txt',
}

# A published package must carry a verification record, not just a licence.
VALIDATION_HEADINGS = ('## How this was verified', '## What remains unverified')

# Credential shapes that must never reach a public package. Prefix-based token
# formats are checked first; the generic assignment rule ignores placeholder
# values so documentation such as `api_key = "your-key-here"` still passes.
SECRET = re.compile(
    r'-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----'                                  # RSA / EC / OPENSSH / PGP / ENCRYPTED
    r'|\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b'        # GitHub
    r'|\bxox[abprs]-[A-Za-z0-9-]{10,}\b'                                        # Slack
    r'|\bsk-(?:proj-|ant-|live-)?[A-Za-z0-9]{24,}\b'                            # OpenAI / Anthropic
    r'|\bAIza[0-9A-Za-z_-]{35}\b'                                               # Google API key
    r'|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b'                                           # AWS access key id
    r'|\bglpat-[A-Za-z0-9_-]{20,}\b|\bnpm_[A-Za-z0-9]{36}\b|\bhf_[A-Za-z0-9]{30,}\b'
    r'|\b[a-z][a-z0-9+.-]*://[^\s"\'/@]+:[^\s"\'/@]{6,}@',            # user:secret@host connection strings
    re.I,
)

ASSIGNED_SECRET = re.compile(
    r'(?:api[_-]?key|token|secret|passwd|password|access[_-]?key|private[_-]?key|client[_-]?secret)'
    r'["\']?\s*[=:]\s*(?:["\'][^"\'\s]{12,}["\']|[A-Za-z0-9/+=_-]{16,})',
    re.I,
)

PLACEHOLDER = re.compile(r'your|example|placeholder|changeme|redact|dummy|sample|xxx|<[^>]*>|\.\.\.|\*\*\*', re.I)

# The GitHub login an approval attributes the submission to, so a consumer can show who
# submitted a package next to who reviewed it: 1-39 letters/digits/single hyphens, never
# leading, trailing or doubled. Junk is refused instead of published as attribution.
GITHUB_LOGIN = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}\Z')

# Every approval is exactly these fields plus `submitted_by`: package ID/version, the
# content hash, the login that submitted the package and the maintainer login, date and
# decision that accepted it.
APPROVAL_FIELDS = {'package_id', 'package_version', 'content_sha256', 'reviewed_by', 'reviewed_at'}

# Machine-local or private-network references. A package must be runnable anywhere,
# so its own author's filesystem, loopback and internal addresses are rejected while
# genuine public names such as `localhost.example.com` are not.
PRIVATE = re.compile(
    r'/Users/[^/\s]+/|/home/[^/\s]+/|file://'
    r'|[A-Za-z]:\\+Users\\|[A-Za-z]:\\+Documents and Settings\\|%USERPROFILE%'
    r'|metadata\.google\.internal|metadata\.goog\b|169\.254\.170\.2|\[?fd00:ec2::254\]?'
    r'|(?<![\w.-])(?:localhost|0\.0\.0\.0|\[::1\]|::1)(?!\.[A-Za-z0-9])'
    r'|(?<![\d.])127\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\.?\d)'
    r'|(?<![\d.])10\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\.?\d)'
    r'|(?<![\d.])192\.168\.\d{1,3}\.\d{1,3}(?!\.?\d)'
    r'|(?<![\d.])172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}(?!\.?\d)'
    r'|(?<![\d.])169\.254\.\d{1,3}\.\d{1,3}(?!\.?\d)'
    r'|\[?(?:fe80|fc[0-9a-f]{2}|fd[0-9a-f]{2}):[0-9a-f:]{2,}\]?',
    re.I,
)


def find_sensitive(text: str):
    """First credential or machine-local reference in `text`, else None.

    Adjacent string literals are joined before matching: a long token split across
    `"ghp_" "AAAA…"` by a formatter or an agent otherwise slips past both the prefix
    rule and the assignment rule.
    """
    joined = re.sub(r'(["\'])\s*\1', '', text) if '" "' in text or "' '" in text else text
    match = SECRET.search(joined) or SECRET.search(text)
    if match:
        return match.group(0)
    for source in (joined, text):
        for match in ASSIGNED_SECRET.finditer(source):
            if not PLACEHOLDER.search(match.group(0)):
                return match.group(0)
    match = PRIVATE.search(joined) or PRIVATE.search(text)
    return match.group(0) if match else None


def read_text(path: Path) -> str:
    """Always UTF-8: the host locale (e.g. a Windows code page) must not decide."""
    return path.read_text(encoding='utf-8')


def write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding='utf-8')


def package_dirs(area: str):
    """Package directories of one area. Dot entries are ignored, stray files are rejected."""
    base = ROOT / area
    for entry in sorted(base.iterdir()):
        if entry.name == '.gitkeep':
            continue
        if entry.name.startswith('.'):
            raise ValueError(
                f'{area}/{entry.name}: dot entries are not validated, so they must not exist '
                'here (a stash of files under packages/ can carry anything past review)'
            )
        if not entry.is_dir():
            raise ValueError(f'{area}/{entry.name}: expected a package directory, found a file')
        yield entry


def validate(folder: Path, demo=False):
    if folder.is_symlink():
        raise ValueError('Package directory must not be a symlink')
    meta = json.loads(read_text(folder / 'metadata.json'))
    Draft202012Validator(SCHEMA).validate(meta)
    if meta['package_id'] != folder.name or meta['demo'] != demo:
        raise ValueError('Directory identity or demo channel mismatch')
    if not demo:
        if 'pending' in meta['license'].lower():
            raise ValueError('License approval is pending')
        license_text = read_text(folder / 'LICENSE')
        lowered = license_text.lower()
        for phrase in ('to be determined', 'redistribution prohibited', 'not authorized',
                       'no license granted'):
            if phrase in lowered:
                raise ValueError(
                    f'LICENSE says `{phrase}`; the declared license must permit redistribution '
                    'of everything in the package'
                )
        if re.search(r'\bTBD\b|placeholder', license_text, re.I):
            raise ValueError('LICENSE is a placeholder; it must be the real license text')
    files = []
    for p in sorted(folder.rglob('*')):
        rel = p.relative_to(folder).as_posix()
        if p.is_symlink():
            raise ValueError(f'Symlink forbidden: {rel}')
        if any(part.startswith('.') or part == '__pycache__' for part in p.relative_to(folder).parts):
            raise ValueError(f'Hidden/generated path forbidden: {rel}')
        if p.is_dir():
            continue
        allowed = rel in ALLOWED_FILES or (rel.startswith('src/') and rel.endswith('.py'))
        if not allowed or '\\' in rel or not p.is_file():
            raise ValueError(f'File not allowlisted: {rel}')
        if p.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f'File too large: {rel}')
        data = p.read_bytes()
        try:
            text = data.decode('utf-8')
        except UnicodeDecodeError as exc:
            raise ValueError(f'{rel}: not valid UTF-8 ({exc.reason} at byte {exc.start})') from exc
        if find_sensitive(text):
            raise ValueError(f'Potential secret/private information: {rel}')
        files.append((rel, data))
    names = {x for x, _ in files}
    reject_case_collisions(names)
    if not {'USAGE.md', 'LICENSE', 'src/requirements.txt'}.issubset(names):
        raise ValueError('Missing USAGE, LICENSE or requirements')
    if len([n for n in names if re.fullmatch(r'src/[^/]+_mcp\.py', n)]) != 1:
        raise ValueError('Exactly one src/*_mcp.py entry point required')
    for line in read_text(folder / 'src/requirements.txt').splitlines():
        if line.strip() and not line.lstrip().startswith('#') and not re.fullmatch(r'[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[A-Za-z0-9_.+!-]+', line.strip()):
            raise ValueError('Use exact package==version requirements; URLs/options unsupported in v1')
    if len(files) > MAX_FILES or sum(len(d) for _, d in files) > MAX_EXPANDED_BYTES:
        raise ValueError('Expanded size/file limit exceeded')
    if not demo:
        _check_validation(folder, names, meta)
    return meta, files


def reject_case_collisions(names) -> None:
    """Two paths differing only by case collide on a case-insensitive filesystem.

    macOS and Windows extractors keep one of them, so a package that looks complete here
    silently loses a module there. The check runs on the names, not on the disk: a
    case-insensitive filesystem cannot even create the pair to be checked.
    """
    folded: dict = {}
    for name in sorted(names):
        key = name.casefold()
        if key in folded:
            raise ValueError(
                f'`{name}` and `{folded[key]}` differ only by case; extracting them on a '
                'case-insensitive filesystem would silently drop one'
            )
        folded[key] = name


def _check_validation(folder: Path, names: set, meta: dict) -> None:
    """A published package records how its tools were verified, and what was not.

    The record is checked for the two required sections and for a mention of every
    declared tool; its content is judged by a human during review.
    """
    if 'VALIDATION.md' not in names:
        raise ValueError(
            'Published packages need VALIDATION.md: record how each tool was verified and '
            'what remains unverified (see AGENTS.md)'
        )
    body = read_text(folder / 'VALIDATION.md')
    missing = [heading for heading in VALIDATION_HEADINGS if heading not in body]
    if missing:
        raise ValueError('VALIDATION.md is missing required section(s): ' + ', '.join(missing))
    unmentioned = [tool for tool in meta['tools'] if tool not in body]
    if unmentioned:
        raise ValueError('VALIDATION.md does not mention every declared tool: '
                         + ', '.join(unmentioned))


def archive(folder, demo=False):
    meta, files = validate(folder, demo)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        # metadata.json travels inside the archive so a ZIP that circulates on its
        # own still carries the paper, commit and license it was built from.
        for name, data in files:
            info = zipfile.ZipInfo(f"{meta['package_id']}/{name}", date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    data = out.getvalue()
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError('Compressed size limit exceeded')
    return meta, data


def reviewed_catalog():
    """Export only content-pinned maintainer approvals. No approvals are created here."""
    ledger = json.loads(read_text(ROOT / 'reviews.json'))
    if ledger.get('schema_version') != 1 or set(ledger) != {'schema_version', 'approvals'}:
        raise ValueError('Invalid review ledger')
    records, outputs, seen = [], {}, set()
    for approval in ledger['approvals']:
        if set(approval) != APPROVAL_FIELDS | {'submitted_by'}:
            raise ValueError('Invalid approval fields')
        package_id = approval['package_id']
        if not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', package_id) or package_id in seen:
            raise ValueError('Invalid/duplicate approved package')
        seen.add(package_id)
        submitted_by = approval['submitted_by']
        if not isinstance(submitted_by, str) or not GITHUB_LOGIN.fullmatch(submitted_by):
            raise ValueError('submitted_by must be a GitHub login')
        if not approval['reviewed_by'] or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', approval['reviewed_at']):
            raise ValueError('Review attribution/date required')
        folder = ROOT / 'packages' / package_id
        if not folder.is_dir():
            raise ValueError(
                f'reviews.json approves `{package_id}`, but packages/{package_id}/ does not '
                'exist; remove the stale approval or restore the package'
            )
        meta, data = archive(folder, False)
        sha = hashlib.sha256(data).hexdigest()
        content = hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(',', ':')).encode() + b'\n' + data).hexdigest()
        if approval['content_sha256'] != content or approval['package_version'] != meta['package_version']:
            raise ValueError('Approved content changed: review again')
        name = f"{package_id}-{meta['package_version']}-{sha[:12]}.zip"
        outputs[name] = data
        records.append({**meta, 'review': approval, 'artifact': {'path': name, 'sha256': sha, 'bytes': len(data)}})
    return records, outputs


def validate_all(require_approved=False):
    """Check examples and packages. A release additionally requires every package to be approved."""
    for folder in package_dirs('examples'):
        validate(folder, True)
    approved = {rec['package_id'] for rec in reviewed_catalog()[0]}
    packages = list(package_dirs('packages'))
    for folder in packages:
        validate(folder, False)
    pending = [p.name for p in packages if p.name not in approved]
    if pending and require_approved:
        raise ValueError('Packages awaiting maintainer approval: ' + ', '.join(pending))
    if pending:
        print(f'warning: {len(pending)} package(s) awaiting maintainer approval: ' + ', '.join(pending))
    return len(packages)


def export_reviewed():
    records, outputs = reviewed_catalog()
    target = ROOT / 'dist' / 'reviewed'
    target.mkdir(parents=True, exist_ok=True)
    for name, data in outputs.items():
        (target / name).write_bytes(data)
    write_text(target / 'index.json', json.dumps({'schema_version': 1, 'channel': 'reviewed-local',
        'source_revision': None, 'packages': records}, ensure_ascii=False, indent=2) + '\n')
    return target


def build(demo=False, revision=None):
    target = ROOT / 'dist' / ('demo' if demo else 'release')
    if demo:
        records, outputs = [], {}
        for folder in package_dirs('examples'):
            meta, data = archive(folder, True)
            sha = hashlib.sha256(data).hexdigest()
            name = f"{meta['package_id']}-{meta['package_version']}-{sha[:12]}.zip"
            outputs[name] = data
            records.append({**meta, 'artifact': {'path': name, 'sha256': sha, 'bytes': len(data)}})
        channel = 'local-demo'
    else:
        try:
            head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True,
                                           stderr=subprocess.DEVNULL).strip()
            dirty = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True,
                                            stderr=subprocess.DEVNULL).strip()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ValueError('A release build needs git with at least one commit and a clean '
                             'working tree; use `catalog.py demo` for a local example catalog.') from exc
        if revision != head or dirty:
            raise ValueError('Release requires exact HEAD and a clean source tree')
        validate_all(require_approved=True)
        records, outputs = reviewed_catalog()
        channel = 'release'
    index = {'schema_version': 1, 'source_revision': revision, 'channel': channel, 'packages': records}
    target.mkdir(parents=True, exist_ok=True)
    # Clear only generated files in the dedicated output directory to avoid stale releases.
    for old in target.iterdir():
        if old.is_file() and (old.suffix == '.zip' or old.name == 'index.json'):
            old.unlink()
    for name, data in outputs.items():
        (target / name).write_bytes(data)
    write_text(target / 'index.json', json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['validate', 'build', 'demo', 'reviewed'])
    parser.add_argument('--revision')
    parser.add_argument('--require-approved', action='store_true',
                        help='validate: every package must carry a matching maintainer approval')
    args = parser.parse_args()
    if args.command == 'reviewed':
        print(export_reviewed())
    elif args.command == 'validate':
        count = validate_all(require_approved=args.require_approved)
        print(f'Static validation passed for {count} package(s)'
              ' (not a security or runtime certification).')
    else:
        print(build(args.command == 'demo', args.revision))
