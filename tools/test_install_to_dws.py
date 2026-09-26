#!/usr/bin/env python3
# Copyright 2026 Alibaba Group
# Licensed under the Apache License, Version 2.0.

"""Ensure installation changes only AICard entries in the shared audit file."""
from pathlib import Path
from contextlib import redirect_stdout
import io
import tempfile
import unittest
from unittest.mock import patch
from subprocess import CompletedProcess

import install_to_dws as install

TEST_TMP = Path(install.ROOT) / ".analysis-artifacts" / "installer-tests"
TEST_TMP.mkdir(parents=True, exist_ok=True)


class LocalAuditTests(unittest.TestCase):
    def test_preflight_requires_card_id_normalizer(self):
        target = 'internal/shortcut/chatmsg/card_update.go'
        seams = [seam for seam in install.SEAMS if seam[0] == target]
        self.assertEqual(1, len(seams))
        for source, expected in (
            (None, 1),
            ('package chatmsg\n', 1),
            ('func NormalizeCardBizID(raw string) string {', 1),
            ('func NormalizeCardBizID(raw string) (string, error) {', 0),
        ):
            with self.subTest(source=source), patch.object(install, 'SEAMS', seams), \
                    patch.object(install.os.path, 'isfile', side_effect=lambda path: (
                        path.endswith('go.mod') or source is not None)), \
                    patch.object(install, 'read', side_effect=lambda path: (
                        'module ' + install.MODULE if path.endswith('go.mod') else source)):
                problems = install.preflight_target('/example/dws')
                self.assertEqual(expected, len(problems))
                if expected:
                    self.assertIn(target, problems[0])

    def test_preflight_requires_aicard_in_builtin_command_map(self):
        seams = [seam for seam in install.SEAMS if seam[0] == 'internal/app/root.go']
        self.assertEqual(1, len(seams))
        for source, expected in (
            ('var builtinCommandNames = map[string]bool{\n "aicard": true, "auth": true,\n}', 0),
            ('var builtinCommandNames = map[string]bool{"auth": true, "aicard": true}', 0),
            ('var builtinCommandNames = map[string]bool{"auth": true}', 1),
            ('var builtinCommandNames = map[string]bool{"aicard": false}', 1),
            ('var builtinCommandNames = map[string]bool{\n // "aicard": true,\n "auth": true,\n}', 1),
            ('var builtinCommandNames = map[string]bool{/* "aicard": true, */ "auth": true}', 1),
            ('/*\nvar builtinCommandNames = map[string]bool{"aicard": true}\n*/', 1),
            ('var example = `\nvar builtinCommandNames = map[string]bool{"aicard": true}\n`', 1),
            ('var builtinCommandNames = map[string]bool{"aicard": /* enabled */ true}', 0),
            ('func example() { var builtinCommandNames = map[string]bool{"aicard": true} }', 1),
            ('var builtinCommandNames = map[string]bool{"aicard": false /* true */}', 1),
            ('var builtinCommandNames = map[string]bool{"aicard": true && false}', 1),
            ('var url = "https://example.com/*literal*/"\nvar builtinCommandNames = map[string]bool{"aicard": true}', 0),
            ('var builtinCommandNames = map[string]bool{"auth": true}\nvar other = map[string]bool{"aicard": true}', 1),
        ):
            with self.subTest(source=source), patch.object(install, 'SEAMS', seams), \
                    patch.object(install.os.path, 'isfile', return_value=True), \
                    patch.object(install, 'read', side_effect=lambda path: (
                        'module ' + install.MODULE if path.endswith('go.mod') else source)):
                self.assertEqual(expected, len(install.preflight_target('/example/dws')))

    def test_source_preflight_enforces_skill_byte_budget(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            directory = Path(root) / install.SKILL_REL
            (directory / 'references').mkdir(parents=True)
            original = (Path(install.SRC) / install.SKILL_REL / 'SKILL.md').read_bytes()
            for size in (install.SKILL_MAX_BYTES, install.SKILL_MAX_BYTES + 1):
                (directory / 'SKILL.md').write_bytes(original + b' ' * (size - len(original)))
                with self.subTest(size=size), patch.object(install, 'SRC', root), \
                        patch.object(install.subprocess, 'run', return_value=CompletedProcess([], 0, '', '')):
                    problems = install.preflight_source()
                    self.assertEqual(size > install.SKILL_MAX_BYTES, bool(problems), problems)

    def test_preflight_requires_new_documentation_api(self):
        product = 'internal/corecmd/contract/product.go'
        seams = [seam for seam in install.SEAMS if seam[0] == product]
        old = 'func RegisterProductDecl(decl ProductDecl) {}\n'
        current = old + ('type HelpDocumentation struct {}\n'
                         'Documentation []HelpDocumentation\n'
                         'func SkillDocumentation(label, skill, relativePath string) HelpDocumentation {}\n')
        for source, expected in ((old, 3), (current, 0)):
            with self.subTest(source=source), patch.object(install, 'SEAMS', seams), \
                    patch.object(install.os.path, 'isfile', return_value=True), \
                    patch.object(install, 'read', side_effect=lambda p: (
                        'module ' + install.MODULE if p.endswith('go.mod') else source)):
                self.assertEqual(len(install.preflight_target('/example/dws')), expected)

    def test_constraint_preflight_accepts_alias_and_struct(self):
        seams = [seam for seam in install.SEAMS if seam[2:] == ("aicard.go", "Constraint structure")]
        for declaration in ("type RuntimeSchemaConstraints = contract.RuntimeSchemaConstraints", "type RuntimeSchemaConstraints struct {"):
            with self.subTest(declaration=declaration), patch.object(install, 'SEAMS', seams), \
                    patch.object(install.os.path, 'isfile', return_value=True), \
                    patch.object(install, 'read', side_effect=lambda path: (
                        'module ' + install.MODULE if path.endswith('go.mod') else declaration)):
                self.assertEqual([], install.preflight_target('/example/dws'))

    def test_gofmt_alignment_preserves_repeat_upgrade_and_uninstall(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            unrelated = '\t\t"other.long.interface.name": "keep",\n'
            for entries in (install.LOCAL_AUDIT_ENTRIES, install.LEGACY_LOCAL_AUDIT_ENTRIES):
                formatted = entries.replace(':     ', ':                 ').replace(':        ', ':                    ')
                original = install.LOCAL_AUDIT_ANCHOR + unrelated + '\t}\n}\n'
                body = original.replace(unrelated, formatted + unrelated)
                path.write_text(body)
                installed, changed = install.local_audit_content(root)
                self.assertEqual(changed, entries == install.LEGACY_LOCAL_AUDIT_ENTRIES)
                path.write_text(installed)
                self.assertEqual((installed, False), install.local_audit_content(root))
                self.assertEqual((original, True), install.local_audit_content(root, remove=True))

    def test_source_inventory_rejects_distribution_junk(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            Path(root, 'module.pyc').write_text('junk')
            with patch.object(install, 'SRC', root), self.assertRaises(SystemExit):
                install.walk_source()

    def test_source_inventory_reports_and_excludes_finder_metadata(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            Path(root, 'nested').mkdir()
            for directory in (Path(root), Path(root, 'nested')):
                (directory / '.DS_Store').write_text('local metadata')
                (directory / 'keep.go').write_text('package example')
            output = io.StringIO()
            with patch.object(install, 'SRC', root), redirect_stdout(output):
                files = install.walk_source()
            self.assertEqual({'keep.go', 'nested/keep.go'}, set(files))
            self.assertEqual(2, output.getvalue().count('Skipping local Finder metadata:'))
            self.assertTrue(Path(root, '.DS_Store').exists())

    def test_source_inventory_includes_single_explain_bundle_and_excludes_evals(self):
        files = install.walk_source()
        self.assertIn('internal/card/a2ui/explain.json', files)
        self.assertFalse(any(name.startswith('internal/card/a2ui/explain/') for name in files))
        self.assertFalse(any('evals/' in name for name in files))

    def test_upgrade_and_remove_previous_audit_wording(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            original = install.LOCAL_AUDIT_ANCHOR + install.LEGACY_LOCAL_AUDIT_ENTRIES + '\t}\n}\n'
            path.write_text(original)
            upgraded, changed = install.local_audit_content(root)
            self.assertTrue(changed)
            self.assertIn(install.LOCAL_AUDIT_ENTRIES, upgraded)
            self.assertNotIn(install.LEGACY_LOCAL_AUDIT_ENTRIES, upgraded)
            removed, changed = install.local_audit_content(root, remove=True)
            self.assertTrue(changed)
            self.assertNotIn('aicard.explain', removed)

    def test_add_repeat_remove_preserve_unrelated_content(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            original = "// existing\n" + install.LOCAL_AUDIT_ANCHOR + '\t\t"other": "keep",\n\t}\n}\n'
            path.write_text(original)
            added, changed = install.local_audit_content(root)
            self.assertTrue(changed)
            self.assertIn('"other": "keep"', added)
            path.write_text(added)
            self.assertEqual((added, False), install.local_audit_content(root))
            self.assertEqual((original, True), install.local_audit_content(root, remove=True))
            path.write_text(original)
            self.assertEqual((original, False), install.local_audit_content(root, remove=True))

    def test_upstream_or_local_conflict_is_never_overwritten(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            path = Path(root) / install.LOCAL_AUDIT
            path.parent.mkdir(parents=True)
            for body in ("changed upstream", install.LOCAL_AUDIT_ANCHOR + '\t"aicard.lint": "custom",\n',
                         install.LOCAL_AUDIT_ANCHOR + install.LOCAL_AUDIT_ENTRIES + '\t"aicard.lint": customReason(),\n'):
                path.write_text(body)
                for remove in (False, True):
                    with self.assertRaises(ValueError):
                        install.local_audit_content(root, remove=remove)
                    self.assertEqual(body, path.read_text())


class VerificationTests(unittest.TestCase):
    def test_unsafe_paths_stop_install_and_uninstall_before_writes(self):
        # Exercise the write entrypoints, including force and copy-only mode.
        for kind in ('leaf', 'parent', 'dangling', 'internal', 'audit', 'dependency', 'traversal', 'absolute', 'stale'):
            for mode in ('install', 'check', 'uninstall'):
                with self.subTest(kind=kind, mode=mode), tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
                    base = Path(root)
                    target = base / 'target'
                    target.mkdir()
                    outside = base / 'outside'
                    outside.mkdir()
                    original = outside / 'module.go'
                    original.write_text('old')
                    source = base / 'source.go'
                    source.write_text('new')
                    rel = 'module.go'
                    if kind == 'parent':
                        (target / 'linked').symlink_to(outside, target_is_directory=True)
                        rel = 'linked/module.go'
                    elif kind in ('leaf', 'dangling', 'internal', 'stale'):
                        referent = original
                        if kind == 'dangling':
                            referent = outside / 'missing.go'
                        elif kind == 'internal':
                            referent = target / 'shared.go'
                            referent.write_text('old')
                        (target / rel).symlink_to(referent)
                    elif kind in ('audit', 'dependency'):
                        link = target / (install.LOCAL_AUDIT if kind == 'audit' else 'go.mod')
                        link.parent.mkdir(parents=True, exist_ok=True)
                        link.symlink_to(original)
                        (target / rel).write_text('old')
                    elif kind == 'traversal':
                        rel = '../outside/module.go'
                    else:
                        rel = str(original)
                    owned = {rel: install.sha256(str(original))}
                    src = {'safe.go': str(source)}
                    if kind != 'stale':
                        src[rel] = str(source)
                    ledger = {'targets': {str(target): {'files': owned, 'localInterfaceAuditOwned': True}}}
                    output = io.StringIO()
                    with patch.object(install, 'load_ledger', return_value=ledger), \
                            patch.object(install, 'save_ledger') as save, \
                            patch.object(install, 'git_head', return_value=''), \
                            patch.object(install, 'preflight_target', return_value=[]), \
                            patch.object(install, 'preflight_source', return_value=[]), \
                            patch.object(install, 'local_audit_content', return_value=('', True)) as audit, \
                            patch.object(install, 'walk_source', return_value=src), \
                            patch.object(install.shutil, 'which', return_value=None), redirect_stdout(output):
                        args = ['--dws', str(target), '--force', '--skip-verify']
                        if mode != 'install':
                            args.append('--' + mode)
                        self.assertEqual(1, install.main(args))
                    save.assert_not_called()
                    audit.assert_not_called()
                    self.assertIn('Unsafe target path', output.getvalue())
                    self.assertEqual('old', original.read_text())
                    self.assertFalse((outside / 'missing.go').exists())
                    self.assertFalse((target / 'safe.go').exists())

    def test_safe_paths_allow_planning_and_uninstallation(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            target = Path(root) / 'target'
            target.mkdir()
            module = target / 'module.go'
            module.write_text('old')
            source = Path(root) / 'source.go'
            source.write_text('new')
            owned = {'module.go': install.sha256(str(module))}
            plan = install.make_plan({'module.go': str(source), 'nested/new.go': str(source)}, str(target), owned)
            self.assertEqual(['update', 'add'], [action for _, action, _ in plan])
            ledger = {'targets': {str(target): {'files': owned}}}
            with patch.object(install, 'save_ledger'), redirect_stdout(io.StringIO()):
                self.assertEqual(0, install.uninstall(str(target), owned, ledger, False))
            self.assertFalse(module.exists())
            self.assertNotIn(str(target), ledger['targets'])

    def test_forced_conflicts_are_counted_as_written_module_files(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP) as root:
            source = Path(root) / 'source.txt'
            source.write_text('new')
            target = Path(root) / 'target'
            target.mkdir()
            for name in ('update', 'conflict', 'same'):
                (target / name).write_text('old' if name != 'same' else 'new')
            src = {name: str(source) for name in ('add', 'update', 'conflict', 'same')}
            ledger = {'targets': {str(target): {'files': {'update': install.sha256(str(target / 'update'))}}}}
            output = io.StringIO()
            with patch.object(install, 'load_ledger', return_value=ledger), \
                    patch.object(install, 'save_ledger'), patch.object(install, 'git_head', return_value=''), \
                    patch.object(install, 'preflight_target', return_value=[]), \
                    patch.object(install, 'preflight_source', return_value=[]), \
                    patch.object(install, 'local_audit_content', return_value=('', False)), \
                    patch.object(install, 'walk_source', return_value=src), \
                    patch.object(install.shutil, 'which', return_value=None), redirect_stdout(output):
                result = install.main(['--dws', str(target), '--force', '--skip-verify'])
            self.assertEqual(0, result)
            self.assertIn('Wrote 3 module files, removed 0 files, unchanged 1 files', output.getvalue())
            for name in src:
                self.assertEqual('new', (target / name).read_text())

    def test_root_discovery_failure_reaches_installer(self):
        def run(command, **kwargs):
            failure = command[:3] == ['go', 'test', './internal/app/']
            if failure:
                self.assertEqual(['-run', '^TestCrossPlatformCoverageAicardRootDiscovery$', '-count=1'], command[3:])
            return CompletedProcess(command, int(failure), 'root discovery regression' if failure else '', '')
        with patch.object(install.shutil, 'which', return_value='/go/bin/go'), \
                patch.object(install.subprocess, 'run', side_effect=run):
            failures = install.verify('/isolated/dws', [])
        self.assertEqual(1, len(failures))
        self.assertIn('root discovery regression', failures[0])

    def test_check_and_uninstall_are_rejected_before_any_target_access(self):
        for args in (("--check", "--uninstall"), ("--uninstall", "--check", "--force")):
            with self.subTest(args=args), patch.object(install, "load_ledger") as ledger, \
                    patch.object(install, "uninstall") as uninstall:
                with self.assertRaises(SystemExit) as caught:
                    install.main(list(args))
                self.assertEqual(2, caught.exception.code)
                ledger.assert_not_called()
                uninstall.assert_not_called()

    def test_default_targets_the_official_sibling_checkout(self):
        self.assertEqual(Path(install.ROOT).parent / 'dingtalk-workspace-cli', Path(install.DEFAULT_DWS))

    def test_validator_tests_are_unfiltered_and_failures_reach_the_installer(self):
        # Execute verify through its subprocess boundary; a failed validator must
        # stop installation verification instead of reporting filtered success.
        for failed in (False, True):
            calls = []
            def run(command, **kwargs):
                calls.append(command)
                failure = failed and command[:3] == ['go', 'test', './internal/card/a2ui/']
                return CompletedProcess(command, int(failure), 'validator regression' if failure else '', '')
            with self.subTest(failed=failed), patch.object(install.shutil, 'which', return_value='/go/bin/go'), \
                    patch.object(install.subprocess, 'run', side_effect=run):
                failures = install.verify('/isolated/dws', [])
            validator = [c for c in calls if c[:3] == ['go', 'test', './internal/card/a2ui/']]
            self.assertEqual([['go', 'test', './internal/card/a2ui/', '-count=1']], validator)
            helper = [c for c in calls if c[:3] == ['go', 'test', './internal/helpers/']]
            self.assertEqual(bool(failures), failed)
            if failed:
                self.assertIn('validator regression', failures[0])
                self.assertEqual([], helper)
            else:
                self.assertEqual([['go', 'test', './internal/helpers/', '-run', 'Aicard', '-count=1']], helper)


if __name__ == "__main__":
    unittest.main()
