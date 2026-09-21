import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('catalog', ROOT / 'tools/catalog.py')
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def write_validation(folder: Path, tools=('sequence_stats',), partial=False):
    """Write the verification record every published package needs."""
    body = ['# Validation', '']
    if not partial:
        body += ['## How this was verified', '']
    body += [f'- `{tool}` exercised through MCP stdio against a synthetic input.' for tool in tools]
    if not partial:
        body += ['', '## What remains unverified', '', '- Nothing beyond the synthetic input.']
    (folder / 'VALIDATION.md').write_text('\n'.join(body) + '\n', encoding='utf-8')


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name) / 'sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
    def tearDown(self):
        self.tmp.cleanup()
    def test_valid_demo(self):
        self.assertEqual(catalog.validate(self.folder, True)[0]['package_id'], 'sequence-stats')
    def test_deterministic_archive(self):
        self.assertEqual(catalog.archive(self.folder, True)[1], catalog.archive(self.folder, True)[1])
    def test_internal_fields_rejected(self):
        p = self.folder / 'metadata.json'
        meta = json.loads(p.read_text()); meta['registry_id'] = 'internal'
        p.write_text(json.dumps(meta))
        with self.assertRaises(ValidationError): catalog.validate(self.folder, True)
    def test_demo_cannot_be_release(self):
        with self.assertRaises(ValueError): catalog.validate(self.folder, False)
    def test_symlink(self):
        (self.folder / 'src/link.py').symlink_to('/etc/hosts')
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)
    def test_forbidden_file(self):
        (self.folder / '.env').write_text('placeholder')
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)
    def test_secret(self):
        (self.folder / 'src/leak.py').write_text('token = "' + 'ghp_' + 'a' * 36 + '"')
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)
    def test_private_path(self):
        (self.folder / 'src/leak.py').write_text('# /Users/person/private')
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)
    def test_dependency_url(self):
        (self.folder / 'src/requirements.txt').write_text('-r https://example.invalid/requirements.txt')
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)
    def test_duplicate_entrypoint(self):
        (self.folder / 'src/other_mcp.py').write_text('# extra')
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)
    def test_missing_usage(self):
        (self.folder / 'USAGE.md').unlink()
        with self.assertRaises(ValueError): catalog.validate(self.folder, True)

class ReviewTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        (self.root / 'packages').mkdir()
        self.folder = self.root / 'packages/sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
        shutil.copytree(ROOT / 'examples/sequence-stats', self.root / 'examples/sequence-stats')
        meta = json.loads((self.folder / 'metadata.json').read_text())
        meta.update(demo=False, repo_url='https://github.com/example/source', commit='a'*40, paper_title='Test fixture only', license='MIT')
        (self.folder / 'metadata.json').write_text(json.dumps(meta))
        write_validation(self.folder)
        meta, data = catalog.archive(self.folder)
        self.record = {'package_id': 'sequence-stats', 'package_version': '0.1.0', 'content_sha256': hashlib.sha256(json.dumps(meta, sort_keys=True, separators=(',', ':')).encode() + b'\n' + data).hexdigest(), 'reviewed_by': 'test-fixture', 'reviewed_at': '2026-09-21'}
    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()
    def ledger(self, approvals):
        (self.root / 'reviews.json').write_text(json.dumps({'schema_version': 1, 'approvals': approvals}))
    def test_unreviewed_is_not_listed(self):
        self.ledger([])
        self.assertEqual(catalog.reviewed_catalog(), ([], {}))
    def test_matching_approval_is_exported(self):
        self.ledger([self.record])
        self.assertEqual(len(catalog.reviewed_catalog()[0]), 1)
    def test_a_submitter_login_is_exported_when_present(self):
        self.ledger([{**self.record, 'submitted_by': 'test-fixture'}])
        self.assertEqual(catalog.reviewed_catalog()[0][0]['review']['submitted_by'], 'test-fixture')
    def test_a_blank_or_junk_submitter_is_refused(self):
        for submitted_by in ('', '  ', 'not a login', '-leading', 'trailing-', 'double--hyphen',
                             'x' * 40, 42):
            record = {**self.record, 'submitted_by': submitted_by}
            with self.subTest(submitted_by=submitted_by):
                self.ledger([record])
                with self.assertRaisesRegex(ValueError, 'submitted_by'):
                    catalog.reviewed_catalog()
    def test_an_unknown_approval_field_is_still_refused(self):
        self.ledger([{**self.record, 'approved_by': 'test-fixture'}])
        with self.assertRaisesRegex(ValueError, 'Invalid approval fields'):
            catalog.reviewed_catalog()
    def test_changed_metadata_invalidates_review(self):
        self.ledger([self.record])
        p = self.folder / 'metadata.json'; meta = json.loads(p.read_text()); meta['summary'] = 'Changed'
        p.write_text(json.dumps(meta))
        with self.assertRaises(ValueError): catalog.reviewed_catalog()
    def test_changed_code_invalidates_review(self):
        self.ledger([self.record])
        with (self.folder / 'src/sequence_stats_mcp.py').open('a') as f: f.write('\n# changed\n')
        with self.assertRaises(ValueError): catalog.reviewed_catalog()


class RobustnessTests(unittest.TestCase):
    """Regressions for defects found during review."""

    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        for area in ('packages', 'examples'):
            (self.root / area).mkdir()
        self.folder = self.root / 'packages/sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
        shutil.copytree(ROOT / 'examples/sequence-stats', self.root / 'examples/sequence-stats')
        meta = json.loads((self.folder / 'metadata.json').read_text())
        meta.update(demo=False, repo_url='https://github.com/example/source', commit='a' * 40,
                    paper_title='Test fixture only', license='MIT')
        (self.folder / 'metadata.json').write_text(json.dumps(meta))
        write_validation(self.folder)
        self.folder.joinpath('src/requirements.txt').write_text('mcp==1.12.4\n')

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def ledger(self, approvals):
        (self.root / 'reviews.json').write_text(json.dumps({'schema_version': 1, 'approvals': approvals}))

    def test_a_stray_file_is_reported_not_crashed(self):
        self.ledger([])
        (self.root / 'packages/NOTES.md').write_text('# notes\n')
        with self.assertRaisesRegex(ValueError, 'expected a package directory'):
            list(catalog.package_dirs('packages'))

    def test_gitkeep_is_skipped_and_other_dot_entries_are_refused(self):
        self.ledger([])
        (self.root / 'packages/.gitkeep').write_text('')
        self.assertEqual([p.name for p in catalog.package_dirs('packages')], ['sequence-stats'])
        # A dot directory is never validated, so files parked in one reach a public PR
        # unscanned. It must be refused rather than ignored.
        (self.root / 'packages/.stash').mkdir()
        (self.root / 'packages/.stash/creds.py').write_text('AWS_SECRET_ACCESS_KEY = "x"\n')
        with self.assertRaisesRegex(ValueError, 'dot entries are not validated'):
            list(catalog.package_dirs('packages'))

    def test_reads_utf8_under_a_non_utf8_locale(self):
        script = Path(catalog.__file__).resolve()
        env = {**__import__('os').environ, 'PYTHONUTF8': '0', 'LC_ALL': 'C', 'LANG': 'C'}
        r = subprocess.run([sys.executable, str(script), 'validate'],
                           cwd=str(ROOT), capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_unapproved_package_warns_but_passes(self):
        self.ledger([])
        self.assertEqual(catalog.validate_all(require_approved=False), 1)

    def test_unapproved_package_fails_the_release_gate(self):
        self.ledger([])
        with self.assertRaisesRegex(ValueError, 'awaiting maintainer approval'):
            catalog.validate_all(require_approved=True)

    def test_approved_package_passes_the_release_gate(self):
        meta, data = catalog.archive(self.folder)
        content = hashlib.sha256(
            json.dumps(meta, sort_keys=True, separators=(',', ':')).encode() + b'\n' + data).hexdigest()
        self.ledger([{'package_id': 'sequence-stats', 'package_version': meta['package_version'],
                      'content_sha256': content, 'reviewed_by': 'test', 'reviewed_at': '2026-09-21'}])
        self.assertEqual(catalog.validate_all(require_approved=True), 1)

    def test_unpinned_or_ranged_requirement_is_rejected(self):
        for line in ('mcp>=1.0', 'mcp', 'git+https://example.com/x.git'):
            (self.folder / 'src/requirements.txt').write_text(line + '\n')
            with self.assertRaisesRegex(ValueError, 'exact package==version'):
                catalog.validate(self.folder, False)

    def test_oversized_file_is_rejected(self):
        (self.folder / 'src/big.py').write_text('#' + 'x' * (catalog.MAX_FILE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'File too large'):
            catalog.validate(self.folder, False)

    def test_notice_is_allowed(self):
        (self.folder / 'NOTICE').write_text('upstream notice\n')
        meta, files = catalog.validate(self.folder, False)
        self.assertIn('NOTICE', {name for name, _ in files})

    def test_credentials_and_private_hosts_are_rejected(self):
        samples = {
            'encrypted key': '-----BEGIN ENCRYPTED PRIVATE KEY-----',
            'slack': 'xoxb-123456789012-abcdefghijklmnop',
            'openai': 'sk-proj-abcdefghijklmnopqrstuvwxyz012345',
            'gitlab': 'glpat-abcdefghijklmnopqrstuv',
            'aws id': 'AKIAIOSFODNN7EXAMPLE',
            'assignment': 'client_secret = "abcdef1234567890"',
            'private ip': 'db_host = 10.1.2.3',
            'metadata ip': '169.254.169.254',
            'ipv6 loopback': 'http://[::1]:8080/admin',
            'file uri': 'file:///srv/data/secret.txt',
        }
        for name, sample in samples.items():
            with self.subTest(name):
                (self.folder / 'src/leak.py').write_text(f'# {sample}\n')
                with self.assertRaisesRegex(ValueError, 'secret/private'):
                    catalog.validate(self.folder, False)

    def test_documentation_placeholders_still_pass(self):
        (self.folder / 'USAGE.md').write_text(
            'Set api_key = "your-key-here" before running. See https://example.com and '
            'https://doi.org/10.1038/s41586-026-11044-y. The risk-management step is optional.\n')
        catalog.validate(self.folder, False)

    def test_demo_build_writes_an_index_without_git(self):
        target = catalog.build(demo=True)
        index = json.loads((target / 'index.json').read_text())
        self.assertEqual(index['channel'], 'local-demo')
        self.assertEqual(len(index['packages']), 1)

    def test_export_reviewed_writes_empty_index(self):
        self.ledger([])
        target = catalog.export_reviewed()
        index = json.loads((target / 'index.json').read_text())
        self.assertEqual(index['channel'], 'reviewed-local')
        self.assertEqual(index['packages'], [])


class ArchiveProvenanceTests(unittest.TestCase):
    def test_metadata_travels_inside_the_archive(self):
        import io as _io
        import zipfile
        meta, data = catalog.archive(ROOT / 'examples/sequence-stats', True)
        names = zipfile.ZipFile(_io.BytesIO(data)).namelist()
        self.assertIn('sequence-stats/metadata.json', names)
        archived = json.loads(zipfile.ZipFile(_io.BytesIO(data)).read('sequence-stats/metadata.json'))
        self.assertEqual(archived['package_id'], meta['package_id'])
        self.assertEqual(archived['license'], meta['license'])


class BoundaryTests(unittest.TestCase):
    """Boundaries that a reviewer must not have to re-check by hand."""

    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        (self.root / 'packages').mkdir(); (self.root / 'examples').mkdir()
        self.folder = self.root / 'packages/sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
        meta = json.loads((self.folder / 'metadata.json').read_text(encoding='utf-8'))
        meta.update(demo=False, repo_url='https://github.com/example/source', commit='a' * 40,
                    paper_title='Fixture', license='MIT', doi='10.1038/s41586-026-11044-y')
        (self.folder / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
        write_validation(self.folder)

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def rewrite(self, **changes):
        p = self.folder / 'metadata.json'
        meta = json.loads(p.read_text(encoding='utf-8'))
        meta.update(changes)
        p.write_text(json.dumps(meta), encoding='utf-8')

    def test_loopback_and_wildcard_binds_are_rejected(self):
        for sample in ('http://127.0.0.1:8080/admin', 'host = 0.0.0.0', 'Connect to localhost.',
                       'bind ::1', 'http://[::1]:9/status'):
            with self.subTest(sample):
                (self.folder / 'src/net.py').write_text(f'# {sample}\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'secret/private'):
                    catalog.validate(self.folder, False)

    def test_public_hostnames_are_not_flagged(self):
        (self.folder / 'src/net.py').write_text(
            '# docs at localhost.example.com; see https://example.com and 2001:db8::1\n', encoding='utf-8')
        catalog.validate(self.folder, False)

    def test_clone_url_and_trailing_slash_are_rejected(self):
        for url in ('https://github.com/example/source.git', 'https://github.com/example/source/'):
            with self.subTest(url):
                self.rewrite(repo_url=url)
                with self.assertRaises(ValidationError):
                    catalog.validate(self.folder, False)

    def test_doi_must_not_carry_sentence_punctuation(self):
        for doi in ('10.1038/s41586-026-11044-y,', '10.1038/x.'):
            with self.subTest(doi):
                self.rewrite(doi=doi)
                with self.assertRaises(ValidationError):
                    catalog.validate(self.folder, False)

    def test_doi_may_be_null_for_a_published_package(self):
        self.rewrite(doi=None)
        catalog.validate(self.folder, False)


class DiagnosticsTests(unittest.TestCase):
    """Error messages a contributor sees when a package is rejected."""

    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        (self.root / 'packages').mkdir(); (self.root / 'examples').mkdir()
        self.folder = self.root / 'packages/sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
        meta = json.loads((self.folder / 'metadata.json').read_text(encoding='utf-8'))
        meta.update(demo=False, repo_url='https://github.com/example/source', commit='a' * 40,
                    paper_title='Fixture', license='MIT')
        (self.folder / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
        write_validation(self.folder)

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_non_utf8_file_names_the_file(self):
        (self.folder / 'src/broken.py').write_bytes(b'# \xff\xfe not utf-8\n')
        with self.assertRaisesRegex(ValueError, r'src/broken\.py: not valid UTF-8'):
            catalog.validate(self.folder, False)

    def test_find_sensitive_always_returns_text(self):
        self.assertIsInstance(catalog.find_sensitive('ghp_' + 'a' * 36), str)
        self.assertIsInstance(catalog.find_sensitive('http://127.0.0.1/x'), str)
        self.assertIsNone(catalog.find_sensitive('nothing to report'))

    def test_windows_user_paths_are_rejected(self):
        for sample in (r'C:\Users\alice\data\x.csv', r'D:\Users\bob\y', '%USERPROFILE%\\data'):
            with self.subTest(sample):
                (self.folder / 'src/win.py').write_text(f'# {sample}\n', encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'secret/private'):
                    catalog.validate(self.folder, False)

    def test_release_build_without_git_explains_itself(self):
        with self.assertRaisesRegex(ValueError, 'needs git with at least one commit'):
            catalog.build(demo=False)


class ValidationRecordTests(unittest.TestCase):
    """A published package carries a verification record; a demo does not need one."""

    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        (self.root / 'packages').mkdir(); (self.root / 'examples').mkdir()
        self.folder = self.root / 'packages/sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
        meta = json.loads((self.folder / 'metadata.json').read_text(encoding='utf-8'))
        meta.update(demo=False, repo_url='https://github.com/example/source', commit='a' * 40,
                    paper_title='Fixture', license='MIT')
        (self.folder / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_missing_record_is_rejected(self):
        (self.folder / 'VALIDATION.md').unlink()      # the example ships one; this case needs none
        with self.assertRaisesRegex(ValueError, 'need VALIDATION.md'):
            catalog.validate(self.folder, False)

    def test_missing_heading_is_rejected(self):
        write_validation(self.folder, partial=True)
        with self.assertRaisesRegex(ValueError, 'missing required section'):
            catalog.validate(self.folder, False)

    def test_tool_must_be_mentioned(self):
        write_validation(self.folder, tools=('something_else',))
        with self.assertRaisesRegex(ValueError, 'does not mention every declared tool'):
            catalog.validate(self.folder, False)

    def test_valid_record_passes(self):
        write_validation(self.folder)
        meta, files = catalog.validate(self.folder, False)
        self.assertIn('VALIDATION.md', {name for name, _ in files})

    def test_demo_package_is_exempt(self):
        meta = json.loads((self.folder / 'metadata.json').read_text(encoding='utf-8'))
        meta['demo'] = True
        (self.folder / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
        catalog.validate(self.folder, True)       # no VALIDATION.md required

    def test_record_ships_inside_the_archive(self):
        write_validation(self.folder)
        import io as _io, zipfile
        _, data = catalog.archive(self.folder, False)
        names = zipfile.ZipFile(_io.BytesIO(data)).namelist()
        self.assertIn('sequence-stats/VALIDATION.md', names)


class FromPaper2McpTests(unittest.TestCase):
    """The bridge that turns a Paper2MCP delivery into a catalog package."""

    SCRIPT = ROOT / 'tools/from_paper2mcp.py'
    PROJECT = ROOT / 'tests/fixtures/paper2mcp-project'
    LICENSE = ROOT / 'LICENSE'

    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        (self.root / 'packages').mkdir(); (self.root / 'examples').mkdir()
        shutil.copytree(ROOT / 'examples/sequence-stats', self.root / 'examples/sequence-stats')
        self.out = self.root / 'packages'

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def run_converter(self, *extra):
        cmd = [sys.executable, str(self.SCRIPT),
               '--project', str(self.PROJECT),
               '--package-id', 'demo-qc',
               '--name', 'Demo QC',
               '--summary', 'Fixture summary',
               '--repo-url', 'https://github.com/example/source',
               '--commit', 'a' * 40,
               '--doi', '10.1186/s13059-017-1382-0',
               '--paper-title', 'Fixture paper',
               '--license', 'Apache-2.0',
               '--license-file', str(self.LICENSE),
               '--python', '3.12',
               '--unverified', 'Numerical agreement beyond the synthetic fixture.',
               '--into', str(self.out),
               *extra]
        return subprocess.run(cmd, capture_output=True, text=True)

    def test_conversion_passes_validation(self):
        r = self.run_converter()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        pkg = self.out / 'demo-qc'
        meta, files = catalog.validate(pkg, False)
        self.assertEqual(meta['tools'], ['qc_summary'])          # from the pipeline inventory
        self.assertEqual(meta['repo_url'], 'https://github.com/example/source')
        names = {name for name, _ in files}
        self.assertEqual(names, {'metadata.json', 'USAGE.md', 'LICENSE', 'NOTICE',
                                 'VALIDATION.md', 'src/requirements.txt', 'src/demoqc_mcp.py'})
        body = (pkg / 'VALIDATION.md').read_text(encoding='utf-8')
        for heading in catalog.VALIDATION_HEADINGS:
            self.assertIn(heading, body)
        self.assertIn('qc_summary', body)
        self.assertIn('Numerical agreement beyond the synthetic fixture.', body)

    def test_reports_tests_and_notebooks_are_not_copied(self):
        self.assertEqual(self.run_converter().returncode, 0)
        copied = {str(p.relative_to(self.out / 'demo-qc')) for p in (self.out / 'demo-qc').rglob('*')}
        for leaked in ('tests/test_science.py', 'notebooks/run.ipynb',
                       'reports/expected-mcp-tools.json', 'reports/delivery-validation.json'):
            self.assertNotIn(leaked, copied)

    def test_unverified_statement_is_required(self):
        cmd = [sys.executable, str(self.SCRIPT), '--project', str(self.PROJECT),
               '--package-id', 'demo-qc', '--name', 'Demo QC', '--summary', 'x',
               '--repo-url', 'https://github.com/example/source', '--commit', 'a' * 40,
               '--paper-title', 'Fixture paper', '--license', 'Apache-2.0',
               '--license-file', str(self.LICENSE), '--python', '3.12', '--into', str(self.out)]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--unverified is required', r.stderr)

    def test_missing_inventory_is_rejected(self):
        stripped = self.root / 'no-reports'
        shutil.copytree(self.PROJECT, stripped)
        (stripped / 'reports/expected-mcp-tools.json').unlink()
        r = self.run_converter('--project', str(stripped))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('tool inventory not found', r.stderr)

    def test_existing_target_is_not_overwritten(self):
        self.assertEqual(self.run_converter().returncode, 0)
        r = self.run_converter()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('already exists', r.stderr)

    def test_clone_url_and_short_commit_are_rejected(self):
        r = self.run_converter('--repo-url', 'https://github.com/example/source.git')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--repo-url', r.stderr)
        r = self.run_converter('--commit', 'abc1234')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--commit', r.stderr)

    def make_project(self, name='project-copy'):
        copy = self.root / name
        shutil.copytree(self.PROJECT, copy)
        return copy

    def test_r_route_material_is_refused(self):
        project = self.make_project()
        scripts = project / 'src/r_scripts'
        scripts.mkdir(parents=True)
        (scripts / 'demoqc.R').write_text('x <- 1\n', encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('non-Python runtime material', r.stderr)
        self.assertIn('src/r_scripts/demoqc.R', r.stderr)

    def test_non_python_route_record_is_refused(self):
        project = self.make_project()
        pipeline = project / '.pipeline'
        pipeline.mkdir()
        (pipeline / 'language.json').write_text(
            json.dumps({'route': 'r', 'reason': 'R implementation'}), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("route 'r'", r.stderr)

    def test_pycache_is_ignored_by_the_route_check(self):
        project = self.make_project()
        cache = project / 'src/__pycache__'
        cache.mkdir()
        (cache / 'demoqc_mcp.cpython-312.pyc').write_bytes(b'\x00\x01')
        r = self.run_converter('--project', str(project))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_missing_runtime_reports_are_refused(self):
        project = self.make_project()
        (project / 'reports/mcp-project-environment.json').unlink()
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('mcp-project-environment.json', r.stderr)
        self.assertFalse((self.out / 'demo-qc').exists())

    def test_failed_runtime_report_is_refused(self):
        project = self.make_project()
        report = project / 'reports/mcp-clean-environment.json'
        data = json.loads(report.read_text(encoding='utf-8'))
        data['success'] = False
        report.write_text(json.dumps(data), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not a successful strict real-call validation', r.stderr)

    def test_runtime_inventory_must_match_the_tool_report(self):
        project = self.make_project()
        report = project / 'reports/mcp-clean-environment.json'
        data = json.loads(report.read_text(encoding='utf-8'))
        data['actual'] = ['qc_summary', 'extra_tool']
        report.write_text(json.dumps(data), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('actual does not match', r.stderr)

    def test_runtime_cases_must_match_the_acceptance_cases(self):
        project = self.make_project()
        report = project / 'reports/mcp-project-environment.json'
        data = json.loads(report.read_text(encoding='utf-8'))
        data['cases'] = data['cases'][:1]
        report.write_text(json.dumps(data), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('case outcomes do not cover', r.stderr)

    def test_runtime_server_must_be_the_entry_point(self):
        project = self.make_project()
        report = project / 'reports/mcp-clean-environment.json'
        data = json.loads(report.read_text(encoding='utf-8'))
        data['server'] = 'src/other_mcp.py'
        report.write_text(json.dumps(data), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not the delivered entry point', r.stderr)

    def test_python_must_be_major_minor_and_match_the_evidence(self):
        r = self.run_converter('--python', '3.12.13')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('--python must be the major.minor', r.stderr)
        self.assertNotIn('Traceback', r.stderr)
        r = self.run_converter('--python', '3.11')
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('not --python 3.11', r.stderr)

    def test_failed_delivery_report_is_refused(self):
        project = self.make_project()
        report = project / 'reports/delivery-validation.json'
        data = json.loads(report.read_text(encoding='utf-8'))
        data['success'] = False
        report.write_text(json.dumps(data), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('successful extraction acceptance', r.stderr)

    def test_missing_tool_positive_case_is_refused(self):
        project = self.make_project()
        cases = project / 'reports/mcp-acceptance-cases.json'
        data = json.loads(cases.read_text(encoding='utf-8'))
        data = [case for case in data if case.get('name') != 'tutorial-reference']
        report = project / 'reports/mcp-project-environment.json'
        env = json.loads(report.read_text(encoding='utf-8'))
        env['cases'] = [outcome for outcome in env['cases'] if outcome['name'] != 'tutorial-reference']
        report.write_text(json.dumps(env), encoding='utf-8')
        clean = project / 'reports/mcp-clean-environment.json'
        data_clean = json.loads(clean.read_text(encoding='utf-8'))
        data_clean['cases'] = [o for o in data_clean['cases'] if o['name'] != 'tutorial-reference']
        clean.write_text(json.dumps(data_clean), encoding='utf-8')
        cases.write_text(json.dumps(data), encoding='utf-8')
        r = self.run_converter('--project', str(project))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('no successful acceptance case', r.stderr)

    def test_delivery_report_requirement_is_documented_in_validation(self):
        self.assertEqual(self.run_converter().returncode, 0)
        body = (self.out / 'demo-qc' / 'VALIDATION.md').read_text(encoding='utf-8')
        self.assertIn('clean environment rebuilt from `src/requirements.txt`', body)



class AuditFindingTests(unittest.TestCase):
    """Regressions for the findings of an independent audit."""

    def setUp(self):
        from unittest.mock import patch
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patch = patch.object(catalog, 'ROOT', self.root); self.patch.start()
        (self.root / 'packages').mkdir(); (self.root / 'examples').mkdir()
        shutil.copytree(ROOT / 'examples/sequence-stats', self.root / 'examples/sequence-stats')
        self.folder = self.root / 'packages/sequence-stats'
        shutil.copytree(ROOT / 'examples/sequence-stats', self.folder)
        meta = json.loads((self.folder / 'metadata.json').read_text(encoding='utf-8'))
        meta.update(demo=False, repo_url='https://github.com/example/source', commit='a' * 40,
                    paper_title='Fixture', license='MIT')
        (self.folder / 'metadata.json').write_text(json.dumps(meta), encoding='utf-8')
        (self.folder / 'LICENSE').write_text('MIT License\n\nPermission is hereby granted...\n')
        write_validation(self.folder)

    def tearDown(self):
        self.patch.stop(); self.tmp.cleanup()

    def test_connection_string_credentials_are_rejected(self):
        (self.folder / 'src/db.py').write_text(
            'DSN = "postgresql://labuser:SuperSecret123@db.example.com:5432/lab"\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'secret/private'):
            catalog.validate(self.folder, False)

    def test_a_token_split_across_string_literals_is_rejected(self):
        (self.folder / 'src/t.py').write_text(
            'TOKEN = ("ghp_" "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'secret/private'):
            catalog.validate(self.folder, False)

    def test_cloud_metadata_hostnames_are_rejected(self):
        (self.folder / 'src/m.py').write_text(
            'URL = "http://metadata.google.internal/computeMetadata/v1/"\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'secret/private'):
            catalog.validate(self.folder, False)

    def test_a_placeholder_licence_file_is_rejected(self):
        (self.folder / 'LICENSE').write_text('To be determined.\n')
        with self.assertRaisesRegex(ValueError, 'LICENSE says'):
            catalog.validate(self.folder, False)
        (self.folder / 'LICENSE').write_text('Placeholder licence.\n')
        with self.assertRaisesRegex(ValueError, 'placeholder'):
            catalog.validate(self.folder, False)

    def test_a_licence_that_forbids_redistribution_is_rejected(self):
        (self.folder / 'LICENSE').write_text('All rights reserved. Redistribution prohibited.\n')
        with self.assertRaisesRegex(ValueError, 'must permit redistribution'):
            catalog.validate(self.folder, False)

    def test_paths_that_differ_only_by_case_are_rejected(self):
        # Checked on the names rather than on disk: a case-insensitive filesystem cannot
        # even hold the colliding pair (the second write overwrites the first), which is
        # the very hazard this guard exists for.
        catalog.reject_case_collisions({'src/helper.py', 'src/requirements.txt'})
        with self.assertRaisesRegex(ValueError, 'differ only by case'):
            catalog.reject_case_collisions({'src/helper.py', 'src/Helper.py'})

    def test_a_stale_approval_is_reported_not_crashed(self):
        (self.root / 'reviews.json').write_text(json.dumps({
            'schema_version': 1,
            'approvals': [{'package_id': 'ghost', 'package_version': '0.1.0',
                           'content_sha256': 'a' * 64, 'reviewed_by': 'someone',
                           'reviewed_at': '2026-09-21'}]}), encoding='utf-8')
        (self.root / 'packages/ghost').exists()
        with self.assertRaisesRegex(ValueError, 'stale approval'):
            catalog.validate_all(require_approved=True)
